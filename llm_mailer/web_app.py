from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Tuple

from flask import Flask, flash, redirect, render_template_string, request, url_for
from werkzeug.utils import secure_filename

from .ai_client import get_provider
from .config import Settings, load_saved_settings, save_settings
from .history import append_history, load_history, mask_api_key, new_entry
from .ingestion import append_manual_from_pdf
from .knowledge_base import KnowledgeBase
from .styles import (
    StylePreset,
    combine_presets,
    default_style_presets,
    load_custom_presets,
    merged_presets,
    save_custom_preset,
)


DATA_DIR = Path(os.environ.get("LLM_MAILER_DATA_DIR", "data"))
DEFAULT_BASE_URLS = [
    "https://api.openai.com/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://api.moonshot.cn/v1",
    "https://api.deepseek.com",
    "https://generativelanguage.googleapis.com/v1beta",
]
MODEL_PROFILES = [
    {"name": "DeepSeek", "base": "https://api.deepseek.com", "models": ["deepseek-chat", "deepseek-coder"]},
    {
        "name": "Gemini",
        "base": "https://generativelanguage.googleapis.com/v1beta",
        "models": ["gemini-1.5-pro-latest", "gemini-1.5-flash-latest"],
    },
    {"name": "OpenAI", "base": "https://api.openai.com/v1", "models": ["gpt-4o", "gpt-4o-mini"]},
]

app = Flask(__name__)
app.secret_key = os.environ.get("LLM_MAILER_SECRET", "dev-secret")


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChatSession:
    id: str
    title: str
    messages: List[ChatMessage]


def _settings_from_form(form) -> Settings:
    saved = load_saved_settings(DATA_DIR)
    base_choice = form.get("base_choice") or None
    base_custom = form.get("base_custom") or None
    base_url = base_custom or base_choice or saved.base_url

    api_key = form.get("api_key") or saved.api_key
    model = form.get("model") or saved.model
    settings = Settings(api_key=api_key, model=model, base_url=base_url)
    save_settings(DATA_DIR, settings)
    return settings


def _chat_file(data_dir: Path) -> Path:
    return data_dir / "chats.json"


def _load_chats(data_dir: Path) -> List[ChatSession]:
    path = _chat_file(data_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) or []
    except json.JSONDecodeError:
        raw = []
    sessions: List[ChatSession] = []
    for item in raw:
        messages = [ChatMessage(role=m.get("role", "user"), content=m.get("content", "")) for m in item.get("messages", [])]
        sessions.append(ChatSession(id=item.get("id", ""), title=item.get("title", "未命名对话"), messages=messages))
    return sessions


def _save_chats(data_dir: Path, sessions: List[ChatSession]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _chat_file(data_dir)
    payload = []
    for session in sessions:
        payload.append({"id": session.id, "title": session.title, "messages": [asdict(m) for m in session.messages]})
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_chat() -> ChatSession:
    return ChatSession(id=uuid.uuid4().hex[:8], title="默认对话", messages=[])


def _ensure_sessions(data_dir: Path, chat_id: str | None = None) -> Tuple[List[ChatSession], ChatSession]:
    sessions = _load_chats(data_dir)
    if not sessions:
        default = _default_chat()
        sessions.append(default)
        _save_chats(data_dir, sessions)
    active = next((s for s in sessions if s.id == chat_id), None) if chat_id else sessions[0]
    if not active:
        active = sessions[0]
    return sessions, active


def _preset_groups(data_dir: Path) -> List[Dict[str, object]]:
    defaults = default_style_presets()
    custom = load_custom_presets(data_dir)
    groups = []
    groups.append({"name": "系统预设组", "presets": [{"key": k, "name": v.name} for k, v in defaults.items()]})
    groups.append({"name": "自定义预设组", "presets": [{"key": k, "name": v.name} for k, v in custom.items()]})
    return groups


def _manual_groups(data_dir: Path) -> Dict[str, List[str]]:
    kb = KnowledgeBase.from_json_files([data_dir / "manuals.json", data_dir / "cases.json"])
    groups: Dict[str, List[str]] = {}
    for doc in kb.documents:
        groups.setdefault(doc.kind, []).append(doc.title)
    return groups


def _build_chat_reply(session: ChatSession, user_message: str, style_keys: List[str], settings: Settings) -> str:
    style_presets = merged_presets(DATA_DIR)
    style = combine_presets(style_presets, style_keys)

    kb = KnowledgeBase.from_json_files([DATA_DIR / "manuals.json", DATA_DIR / "cases.json"])
    references = kb.search(user_message, limit=4)

    history_text = "\n".join([f"{m.role}: {m.content}" for m in session.messages[-6:]]) or "（无历史）"
    refs_text = "\n".join([f"- {doc.title}｜{doc.kind}" for doc in references]) or "- 无匹配文档"

    prompt = f"""
你是一名邮件沟通助手，以对话模式回答并保持友好、清晰的表达。
当前风格：{style.name}
语气：{style.tone}
结构参考：{' | '.join(style.structure)}
结尾参考：{style.closing}
提醒：{' / '.join(style.reminders)}

最近对话：
{history_text}

相关说明书：
{refs_text}

用户消息：{user_message}
请给出下一条回复，保持中文输出，并可引用相关说明书要点。
"""
    provider = get_provider(settings)
    return provider.generate(prompt)


def _render(reply: str | None = None, message: str | None = None, selected_styles: list[str] | None = None, chat_id: str | None = None):
    styles = merged_presets(DATA_DIR)
    settings = load_saved_settings(DATA_DIR)
    history = load_history(DATA_DIR)
    sessions, active = _ensure_sessions(DATA_DIR, chat_id)

    selected_styles = selected_styles or ["concise_business"]

    template = """
<!doctype html>
<html lang=\"zh\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>LLM 邮件助手 - 对话模式</title>
    <style>
      :root {
        --bg: #0b1220;
        --panel: #0f172a;
        --card: #111827;
        --border: #1f2937;
        --text: #e5e7eb;
        --muted: #9ca3af;
        --accent: #8b5cf6;
        --accent-2: #22d3ee;
        --danger: #f87171;
      }
      * { box-sizing: border-box; }
      body {
        font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
        background: radial-gradient(circle at 16% 20%, rgba(34,211,238,0.09), transparent 22%),
                    radial-gradient(circle at 84% 10%, rgba(139,92,246,0.12), transparent 25%),
                    var(--bg);
        color: var(--text);
        margin: 0;
        min-height: 100vh;
        padding: 18px 18px 32px;
        display: flex;
        flex-direction: column;
        gap: 14px;
      }
      h1 { margin: 0; font-size: 30px; }
      h2 { margin: 0; font-size: 20px; }
      h3 { margin: 0 0 6px; font-size: 16px; }
      a { color: var(--accent-2); text-decoration: none; }
      .top-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
      .pill { display: inline-flex; align-items: center; padding: 6px 10px; background: rgba(34,211,238,0.12); color: #a5f3fc; border-radius: 999px; font-size: 12px; }
      .widget { background: linear-gradient(145deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01)); border: 1px solid var(--border); border-radius: 14px; padding: 12px; box-shadow: 0 12px 40px rgba(0,0,0,0.3); }
      .widget details { background: rgba(255,255,255,0.02); border: 1px solid #1f2937; border-radius: 10px; padding: 8px 10px; }
      .widget summary { cursor: pointer; font-weight: 700; }
      .muted { color: var(--muted); font-size: 13px; }
      .chip { display: inline-flex; padding: 4px 8px; border-radius: 999px; background: rgba(255,255,255,0.08); font-size: 12px; }
      .list-inline { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
      .list-inline span { padding: 4px 8px; border-radius: 10px; background: rgba(255,255,255,0.06); font-size: 12px; }
      .chat-layout { display: grid; grid-template-columns: 280px 1fr; gap: 14px; background: var(--panel); border: 1px solid var(--border); border-radius: 16px; padding: 12px; box-shadow: 0 16px 40px rgba(0,0,0,0.35); }
      .chat-list { border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; }
      .chat-item { padding: 10px; border-radius: 10px; cursor: pointer; border: 1px solid transparent; background: rgba(255,255,255,0.02); }
      .chat-item.active { border-color: var(--accent-2); background: rgba(34,211,238,0.08); }
      .chat-main { display: flex; flex-direction: column; gap: 10px; min-height: 60vh; }
      .chat-window { flex: 1; background: #0b1220; border: 1px solid var(--border); border-radius: 12px; padding: 12px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
      .msg { max-width: 78%; padding: 10px 12px; border-radius: 12px; line-height: 1.5; box-shadow: inset 0 1px 0 rgba(255,255,255,0.03); }
      .msg.user { margin-left: auto; background: linear-gradient(120deg, var(--accent), var(--accent-2)); color: #0b1220; }
      .msg.assistant { margin-right: auto; background: #111827; border: 1px solid var(--border); }
      .chat-form { background: #0d1628; border: 1px solid var(--border); border-radius: 12px; padding: 12px; display: grid; grid-template-columns: 1fr 260px; gap: 10px; align-items: end; }
      .chat-form textarea { width: 100%; min-height: 100px; background: #0b1220; border: 1px solid var(--border); color: var(--text); border-radius: 10px; padding: 10px; }
      .chat-form .controls { display: grid; gap: 8px; }
      select, input { width: 100%; background: #0b1220; border: 1px solid var(--border); color: var(--text); padding: 9px 10px; border-radius: 10px; }
      button { border: none; padding: 10px 14px; border-radius: 10px; font-weight: 700; cursor: pointer; }
      button.primary { background: linear-gradient(90deg, var(--accent), var(--accent-2)); color: #0b1220; box-shadow: 0 10px 28px rgba(34,211,238,0.35); }
      button.secondary { background: #1f2937; color: var(--text); }
      form.inline { display: flex; gap: 8px; }
      .flash { color: var(--danger); font-weight: 700; }
      .message { color: #34d399; font-weight: 700; }
      .history-card { background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 12px; padding: 10px; }
      @media (max-width: 960px) { .chat-layout { grid-template-columns: 1fr; } .chat-form { grid-template-columns: 1fr; } .chat-list { border-right: none; border-bottom: 1px solid var(--border); padding-bottom: 10px; } }
    </style>
  </head>
  <body>
    <div style=\"display:flex;justify-content:space-between;align-items:center;gap:10px;\">
      <div>
        <h1>LLM 邮件助手 · 对话模式</h1>
        <div class=\"muted\">顶部小窗归纳预设组、说明书组、API 模型；底部对话框支持多会话切换。</div>
      </div>
      <div class=\"pill\">数据目录：{{ data_dir }}</div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}<div class=\"flash\">{{ messages[0] }}</div>{% endif %}
    {% endwith %}
    {% if message %}<div class=\"message\">{{ message }}</div>{% endif %}

    <div class=\"top-row\">
      <div class=\"widget\">
        <details open>
          <summary>预设组</summary>
          {% for group in preset_groups %}
            <div style=\"margin-top:6px;\"><strong>{{ group.name }}</strong></div>
            <div class=\"list-inline\">
              {% for item in group.presets %}<span>{{ item.key }}｜{{ item.name }}</span>{% endfor %}
            </div>
          {% endfor %}
          <form method=\"post\" action=\"{{ url_for('save_preset') }}\" style=\"margin-top:8px; display:grid; gap:6px;\">
            <input name=\"preset_key\" placeholder=\"新增预设 Key\" required />
            <input name=\"preset_name\" placeholder=\"显示名称\" required />
            <input name=\"preset_tone\" placeholder=\"语气描述\" required />
            <input name=\"preset_closing\" placeholder=\"结尾语\" required />
            <textarea name=\"preset_structure\" rows=\"2\" placeholder=\"结构（每行一条）\"></textarea>
            <textarea name=\"preset_reminders\" rows=\"2\" placeholder=\"提醒（每行一条）\"></textarea>
            <button class=\"secondary\" type=\"submit\">保存/覆盖预设</button>
          </form>
        </details>
      </div>

      <div class=\"widget\">
        <details open>
          <summary>说明书组</summary>
          {% if manual_groups %}
            {% for kind, titles in manual_groups.items() %}
              <div style=\"margin-top:6px;\"><strong>{{ kind }}</strong> <span class=\"chip\">{{ titles|length }} 篇</span></div>
              <div class=\"list-inline\">{% for title in titles %}<span>{{ title }}</span>{% endfor %}</div>
            {% endfor %}
          {% else %}
            <div class=\"muted\">暂无说明书或案例，请上传 PDF 入库。</div>
          {% endif %}
          <form method=\"post\" action=\"{{ url_for('upload') }}\" enctype=\"multipart/form-data\" style=\"margin-top:8px; display:grid; gap:6px;\">
            <input type=\"file\" name=\"manual_pdf\" accept=\"application/pdf\" />
            <button class=\"secondary\" type=\"submit\">上传并入库</button>
          </form>
        </details>
      </div>

      <div class=\"widget\">
        <details open>
          <summary>API & 模型</summary>
          <div class=\"muted\">已保存：Base {{ settings.base_url or '未设置' }} ｜ 模型 {{ settings.model or '未设置' }} ｜ Key {{ masked_key or '未保存' }}</div>
          <div style=\"margin-top:8px;\"><strong>兼容 Base</strong></div>
          <div class=\"list-inline\">{% for base in default_bases %}<span>{{ base }}</span>{% endfor %}</div>
          <div style=\"margin-top:8px;\"><strong>模型示例</strong></div>
          {% for item in model_profiles %}
            <div class=\"history-card\" style=\"margin-top:6px;\">
              <div><strong>{{ item.name }}</strong> ｜ {{ item.base }}</div>
              <div class=\"muted\">{{ item.models|join(', ') }}</div>
            </div>
          {% endfor %}
          <form method=\"post\" action=\"{{ url_for('save_api_settings') }}\" style=\"margin-top:8px; display:grid; gap:6px;\">
            <input name=\"base_custom\" placeholder=\"API Base\" value=\"{{ settings.base_url or '' }}\" />
            <input name=\"model\" placeholder=\"模型\" value=\"{{ settings.model or '' }}\" />
            <input name=\"api_key\" type=\"password\" placeholder=\"API Key\" value=\"{{ settings.api_key or '' }}\" />
            <button class=\"secondary\" type=\"submit\">保存 API 信息</button>
          </form>
        </details>
      </div>
    </div>

    <div class=\"chat-layout\">
      <div class=\"chat-list\">
        <form method=\"post\" action=\"{{ url_for('new_chat') }}\" class=\"inline\">
          <input name=\"chat_title\" placeholder=\"新对话标题\" />
          <button class=\"secondary\" type=\"submit\">新建</button>
        </form>
        {% for session in sessions %}
          <a class=\"chat-item {% if session.id == active.id %}active{% endif %}\" href=\"{{ url_for('index', chat_id=session.id) }}\">{{ session.title }}</a>
        {% endfor %}
      </div>

      <div class=\"chat-main\">
        <div class=\"chat-window\">
          {% if not active.messages %}
            <div class=\"muted\">暂无对话，输入内容开始交流。</div>
          {% endif %}
          {% for msg in active.messages %}
            <div class=\"msg {{ msg.role }}\">{{ msg.content }}</div>
          {% endfor %}
        </div>
        <form class=\"chat-form\" method=\"post\" action=\"{{ url_for('send_message') }}\">
          <input type=\"hidden\" name=\"chat_id\" value=\"{{ active.id }}\" />
          <textarea name=\"message\" placeholder=\"输入邮件需求或和助手聊天，回复将展示在下方对话框...\" required>{{ last_message }}</textarea>
          <div class=\"controls\">
            <select name=\"style_keys\" multiple size=\"4\">
              {% for key, preset in styles.items() %}
                <option value=\"{{ key }}\" {% if key in selected_styles %}selected{% endif %}>{{ key }}｜{{ preset.name }}</option>
              {% endfor %}
            </select>
            <select name=\"base_choice\">
              <option value=\"\">选择兼容 Base（可选）</option>
              {% for base in default_bases %}
                <option value=\"{{ base }}\" {% if settings.base_url == base %}selected{% endif %}>{{ base }}</option>
              {% endfor %}
            </select>
            <input name=\"base_custom\" placeholder=\"自定义 Base\" value=\"{{ settings.base_url or '' }}\" />
            <input name=\"model\" placeholder=\"模型名称\" value=\"{{ settings.model or '' }}\" />
            <input name=\"api_key\" type=\"password\" placeholder=\"API Key\" value=\"{{ settings.api_key or '' }}\" />
            <button class=\"primary\" type=\"submit\">发送并生成回复</button>
          </div>
        </form>
      </div>
    </div>

    <div class=\"widget\" style=\"margin-top:6px;\">
      <div style=\"display:flex;justify-content:space-between;align-items:center;\">
        <h3>最近操作</h3><span class=\"chip\">最新 50 条</span>
      </div>
      {% if not history %}
        <div class=\"muted\">暂无历史操作。</div>
      {% endif %}
      <div style=\"display:grid;gap:8px;\">
        {% for item in history %}
          <div class=\"history-card\">
            <div><strong>{{ item.action }}</strong> ｜ {{ item.timestamp }}</div>
            <div class=\"muted\">{{ item.detail.get('message', item.detail.get('email', '')) }}</div>
          </div>
        {% endfor %}
      </div>
      {% if reply %}
        <div class=\"history-card\" style=\"margin-top:8px;\">
          <div><strong>最新生成</strong></div>
          <pre style=\"white-space:pre-wrap;word-break:break-word;\">{{ reply }}</pre>
        </div>
      {% endif %}
    </div>
  </body>
</html>
"""

    return render_template_string(
        template,
        data_dir=DATA_DIR,
        styles=styles,
        settings=settings,
        history=history,
        reply=reply,
        message=message,
        preset_groups=_preset_groups(DATA_DIR),
        manual_groups=_manual_groups(DATA_DIR),
        masked_key=mask_api_key(settings.api_key),
        default_bases=DEFAULT_BASE_URLS,
        model_profiles=MODEL_PROFILES,
        sessions=sessions,
        active=active,
        selected_styles=selected_styles,
        last_message="",
    )


@app.route("/", methods=["GET"])
def index():
    chat_id = request.args.get("chat_id")
    return _render(chat_id=chat_id)


@app.route("/chat/new", methods=["POST"])
def new_chat():
    title = (request.form.get("chat_title") or "").strip()
    sessions = _load_chats(DATA_DIR)
    session = ChatSession(id=uuid.uuid4().hex[:8], title=title or f"会话 {len(sessions)+1}", messages=[])
    sessions.insert(0, session)
    _save_chats(DATA_DIR, sessions)
    return redirect(url_for("index", chat_id=session.id))


@app.route("/chat/send", methods=["POST"])
def send_message():
    user_message = (request.form.get("message") or "").strip()
    if not user_message:
        flash("请输入内容后再发送。")
        return redirect(url_for("index"))

    chat_id = request.form.get("chat_id")
    style_keys = request.form.getlist("style_keys") or ["concise_business"]
    settings = _settings_from_form(request.form)

    sessions, active = _ensure_sessions(DATA_DIR, chat_id)
    active.messages.append(ChatMessage(role="user", content=user_message))

    reply = _build_chat_reply(active, user_message, style_keys, settings)
    active.messages.append(ChatMessage(role="assistant", content=reply))
    _save_chats(DATA_DIR, sessions)

    append_history(
        DATA_DIR,
        new_entry(
            "chat_message",
            {
                "chat_id": active.id,
                "message": user_message,
                "style_keys": style_keys,
                "settings": {
                    "base_url": settings.base_url,
                    "model": settings.model,
                    "api_key": mask_api_key(settings.api_key),
                },
                "reply_preview": reply[:200],
            },
        ),
    )
    return _render(reply=reply, selected_styles=style_keys, chat_id=active.id)


@app.route("/preset", methods=["POST"])
def save_preset():
    key = (request.form.get("preset_key") or "").strip()
    if not key:
        flash("请填写预设 Key。")
        return redirect(url_for("index"))

    name = (request.form.get("preset_name") or "").strip()
    tone = (request.form.get("preset_tone") or "").strip()
    closing = (request.form.get("preset_closing") or "").strip()
    structure_raw = request.form.get("preset_structure") or ""
    reminders_raw = request.form.get("preset_reminders") or ""

    if not all([name, tone, closing]):
        flash("请完整填写名称、语气与结尾。")
        return redirect(url_for("index"))

    def _lines(raw: str) -> list[str]:
        return [line.strip() for line in raw.replace("\r", "").split("\n") if line.strip()]

    preset = StylePreset(
        name=name,
        tone=tone,
        structure=_lines(structure_raw) or ["致谢/问候", "回复要点", "后续行动"],
        closing=closing,
        reminders=_lines(reminders_raw) or ["保持简洁", "明确可执行事项"],
    )

    save_custom_preset(DATA_DIR, key, preset)
    message = f"已保存预设：{key}（{name}），在上方列表可直接选择/组合。"
    append_history(
        DATA_DIR,
        new_entry(
            "preset_saved",
            {"key": key, "name": name, "tone": tone, "structure": preset.structure},
        ),
    )
    return _render(message=message, selected_styles=[key])


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("manual_pdf")
    if not file or not file.filename:
        flash("请选择要上传的 PDF 文件。")
        return redirect(url_for("index"))

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".pdf"):
        flash("仅支持 PDF 文件上传。")
        return redirect(url_for("index"))

    uploads_dir = DATA_DIR / "manual_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    save_path = uploads_dir / filename
    file.save(save_path)

    entry = append_manual_from_pdf(save_path, DATA_DIR)
    message = f"已入库：{entry['title']}（来源：{save_path}）"
    append_history(
        DATA_DIR,
        new_entry(
            "manual_upload",
            {"filename": filename, "title": entry["title"], "source": str(save_path)},
        ),
    )
    return _render(message=message)


@app.route("/api_settings", methods=["POST"])
def save_api_settings():
    settings = _settings_from_form(request.form)
    append_history(
        DATA_DIR,
        new_entry(
            "api_saved",
            {
                "base_url": settings.base_url,
                "model": settings.model,
                "api_key": mask_api_key(settings.api_key),
            },
        ),
    )
    return _render(message="已保存 API 配置信息")


def main() -> None:
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)


if __name__ == "__main__":
    main()
