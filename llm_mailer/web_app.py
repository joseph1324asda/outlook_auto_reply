from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, flash, redirect, render_template_string, request, url_for
from werkzeug.utils import secure_filename

from .cli import draft_email_reply
from .config import Settings, load_saved_settings, save_settings
from .history import append_history, load_history, mask_api_key, new_entry
from .ingestion import append_manual_from_pdf
from .styles import StylePreset, merged_presets, save_custom_preset


DATA_DIR = Path(os.environ.get("LLM_MAILER_DATA_DIR", "data"))
DEFAULT_BASE_URLS = [
    "https://api.openai.com/v1",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://api.moonshot.cn/v1",
]

app = Flask(__name__)
app.secret_key = os.environ.get("LLM_MAILER_SECRET", "dev-secret")


def _attachments_from_form(raw: str | None) -> list[str]:
    if not raw:
        return []
    lines = [item.strip() for item in raw.replace("\r", "").split("\n")]
    return [item for item in lines if item]


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


def _render(
    reply: str | None = None,
    message: str | None = None,
    selected_styles: list[str] | None = None,
):
    styles = merged_presets(DATA_DIR)
    settings = load_saved_settings(DATA_DIR)
    history = load_history(DATA_DIR)

    last_draft = next((entry for entry in history if entry.action == "draft"), None)
    selected_styles = (
        selected_styles
        or (last_draft.detail.get("style_keys") if last_draft else None)
        or ["concise_business"]
    )

    email_value = (last_draft.detail.get("email") if last_draft else "") or ""
    priority_value = (last_draft.detail.get("priority") if last_draft else "") or ""
    attachments_text = "\n".join(last_draft.detail.get("attachments", [])) if last_draft else ""

    template = """
<!doctype html>
<html lang=\"zh\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>LLM 邮件助手</title>
    <style>
      :root {
        --bg: #0f172a;
        --panel: #111827;
        --card: #1f2937;
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
        background: radial-gradient(circle at 20% 20%, rgba(34,211,238,0.08), transparent 20%),
                    radial-gradient(circle at 80% 0%, rgba(139,92,246,0.14), transparent 25%),
                    var(--bg);
        color: var(--text);
        margin: 0 auto;
        max-width: 1180px;
        padding: 24px 18px 48px;
      }
      h1 { margin: 0; font-size: 32px; letter-spacing: 0.2px; }
      h2 { margin: 0 0 8px; font-size: 20px; }
      p { color: var(--muted); line-height: 1.5; }
      a { color: var(--accent-2); text-decoration: none; }
      .grid { display: grid; grid-template-columns: 320px 1fr; gap: 18px; align-items: start; }
      .panel { background: linear-gradient(145deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01)); border: 1px solid #1f2937; border-radius: 16px; padding: 16px; box-shadow: 0 12px 40px rgba(0,0,0,0.25); backdrop-filter: blur(6px); }
      .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 14px 16px; box-shadow: inset 0 1px 0 rgba(255,255,255,0.03); }
      label { display: block; font-weight: 600; margin-top: 10px; }
      textarea, input, select {
        width: 100%;
        margin-top: 6px;
        background: #0b1220;
        border: 1px solid #1f2937;
        color: var(--text);
        padding: 10px 12px;
        border-radius: 10px;
        font-size: 14px;
        transition: border 0.15s ease, box-shadow 0.15s ease;
      }
      textarea:focus, input:focus, select:focus { outline: none; border-color: var(--accent-2); box-shadow: 0 0 0 2px rgba(34,211,238,0.15); }
      textarea { min-height: 120px; }
      select[multiple] { min-height: 160px; }
      button {
        border: none;
        padding: 10px 16px;
        border-radius: 10px;
        font-weight: 700;
        color: white;
        cursor: pointer;
        margin-top: 12px;
        background: linear-gradient(90deg, var(--accent), var(--accent-2));
        box-shadow: 0 8px 25px rgba(139,92,246,0.35);
      }
      button.secondary { background: #374151; box-shadow: none; }
      .chip { display: inline-block; padding: 4px 10px; border-radius: 999px; background: rgba(255,255,255,0.08); color: var(--text); font-size: 12px; }
      .muted { color: var(--muted); font-size: 13px; }
      .pill { display: inline-flex; align-items: center; padding: 6px 10px; background: rgba(34,211,238,0.1); color: #a5f3fc; border-radius: 999px; font-size: 12px; margin-right: 6px; }
      .section-title { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
      .layout { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
      .history-list { display: flex; flex-direction: column; gap: 10px; }
      .history-item { border: 1px solid #1f2937; border-radius: 12px; padding: 12px; background: #0b1220; }
      .history-item .title { font-weight: 700; }
      pre { white-space: pre-wrap; word-break: break-word; background: #0b1220; border-radius: 12px; padding: 12px; border: 1px solid #1f2937; }
      .flash { color: var(--danger); font-weight: 600; margin-bottom: 8px; }
      .message { color: #34d399; font-weight: 600; margin-bottom: 8px; }
      .preset-card { border: 1px solid #1f2937; border-radius: 12px; padding: 10px 12px; background: #0b1220; }
      hr { border: none; border-top: 1px solid #1f2937; margin: 12px 0; }
      @media (max-width: 960px) { .grid { grid-template-columns: 1fr; } }
    </style>
  </head>
  <body>
    <div class=\"section-title\">
      <div>
        <h1>LLM 邮件助手</h1>
        <p>保存预设、API 信息和历史操作记录，界面布局参考 SillyTavern，左侧为控制面板，右侧为结果与历史。</p>
      </div>
      <div class=\"pill\">数据目录：{{ data_dir }}</div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class=\"flash\">{{ messages[0] }}</div>
      {% endif %}
    {% endwith %}
    {% if message %}<div class=\"message\">{{ message }}</div>{% endif %}

    <div class=\"grid\">
      <div class=\"panel\">
        <form method=\"post\" action=\"{{ url_for('draft') }}\"> 
          <div class=\"section-title\">
            <h2>生成邮件草稿</h2>
            <span class=\"chip\">风格可多选</span>
          </div>
          <label for=\"email\">邮件摘要或正文</label>
          <textarea id=\"email\" name=\"email\" rows=\"4\" required placeholder=\"例如：客户反馈接口限流，希望提升到每分钟120次\">{{ email_value }}</textarea>

          <label for=\"style_keys\">选择/组合风格预设</label>
          <select id=\"style_keys\" name=\"style_keys\" multiple size=\"6\">
            {% for key, preset in styles.items() %}
              <option value=\"{{ key }}\" {% if key in selected_styles %}selected{% endif %}>{{ key }}｜{{ preset.name }}</option>
            {% endfor %}
          </select>
          <div class=\"muted\">按住 Ctrl/Cmd 可多选，组合后自动合并语气、结构与提醒。</div>

          <label for=\"priority\">客户优先级（可选）</label>
          <input id=\"priority\" name=\"priority\" placeholder=\"例如：P1\" value=\"{{ priority_value }}\" />

          <label for=\"attachments\">附件名称（换行分隔，可选）</label>
          <textarea id=\"attachments\" name=\"attachments\" rows=\"2\" placeholder=\"报告.pdf\\n日志.txt\">{{ attachments_text }}</textarea>

          <div class=\"section-title\" style=\"margin-top: 12px;\">
            <h3>API 配置</h3>
            <span class=\"chip\">自动保存</span>
          </div>
          <label for=\"base_choice\">常用 API Base</label>
          <select id=\"base_choice\" name=\"base_choice\">
            <option value=\"\">（使用自定义或环境变量）</option>
            {% for base in default_bases %}
              <option value=\"{{ base }}\" {% if settings.base_url == base %}selected{% endif %}>{{ base }}</option>
            {% endfor %}
          </select>

          <label for=\"base_custom\">自定义 API Base URL</label>
          <input id=\"base_custom\" name=\"base_custom\" placeholder=\"例如：https://api.your-llm.com/v1\" value=\"{{ settings.base_url or '' }}\" />

          <label for=\"model\">模型名称</label>
          <input id=\"model\" name=\"model\" placeholder=\"例如：gpt-4o-mini\" value=\"{{ settings.model or '' }}\" />

          <label for=\"api_key\">API Key（保存在本地 app_settings.json）</label>
          <input id=\"api_key\" name=\"api_key\" type=\"password\" placeholder=\"sk-...\" value=\"{{ settings.api_key or '' }}\" />

          <button type=\"submit\">一键生成草稿</button>
        </form>

        <hr />

        <form method=\"post\" action=\"{{ url_for('upload') }}\" enctype=\"multipart/form-data\">
          <div class=\"section-title\">
            <h2>说明书库</h2>
            <span class=\"chip\">PDF 入库</span>
          </div>
          <label for=\"manual_pdf\">选择 PDF 文件</label>
          <input type=\"file\" id=\"manual_pdf\" name=\"manual_pdf\" accept=\"application/pdf\" required />
          <button type=\"submit\" class=\"secondary\">上传并入库</button>
        </form>
      </div>

      <div class=\"panel\" style=\"background: linear-gradient(160deg, rgba(34,211,238,0.05), rgba(139,92,246,0.12));\">
        <div class=\"section-title\">
          <h2>即时概览</h2>
          <span class=\"chip\">上次操作</span>
        </div>
        <div class=\"card\">
          <div class=\"muted\">当前 API 配置</div>
          <div>Base：{{ settings.base_url or '未设置' }}</div>
          <div>模型：{{ settings.model or '未设置' }}</div>
          <div>Key：{{ masked_key or '未保存' }}</div>
        </div>

        {% if history %}
          <div class=\"card\" style=\"margin-top: 10px;\">
            <div class=\"muted\">最近记录</div>
            <div class=\"title\">{{ history[0].action }}</div>
            <div class=\"muted\">{{ history[0].timestamp }}</div>
            <div style=\"margin-top: 6px;\">{{ history[0].detail.get('email', history[0].detail.get('message', '')) }}</div>
          </div>
        {% endif %}

        {% if reply %}
          <div class=\"card\" style=\"margin-top: 12px;\">
            <div class=\"section-title\">
              <h3>生成结果</h3>
              <span class=\"pill\">可直接复制</span>
            </div>
            <pre>{{ reply }}</pre>
          </div>
        {% endif %}

        <div class=\"card\" style=\"margin-top: 12px;\">
          <div class=\"section-title\">
            <h3>历史记录</h3>
            <span class=\"chip\">最新 50 条</span>
          </div>
          <div class=\"history-list\">
            {% if not history %}
              <div class=\"muted\">暂无历史操作，生成草稿或保存预设后会自动记录。</div>
            {% endif %}
            {% for item in history %}
              <div class=\"history-item\">
                <div class=\"title\">{{ item.action }} <span class=\"chip\" style=\"margin-left: 6px;\">{{ item.timestamp }}</span></div>
                {% if item.action == 'draft' %}
                  <div class=\"muted\">摘要：{{ item.detail.get('email', '') }}</div>
                  <div class=\"muted\">风格：{{ item.detail.get('style_keys', [])|join(', ') }}</div>
                  <div class=\"muted\">优先级：{{ item.detail.get('priority') or '无' }}｜附件：{{ item.detail.get('attachments', [])|join(' / ') or '无' }}</div>
                {% elif item.action == 'preset_saved' %}
                  <div class=\"muted\">预设：{{ item.detail.get('key') }}｜{{ item.detail.get('name') }}</div>
                  <div class=\"muted\">语气：{{ item.detail.get('tone') }}</div>
                {% elif item.action == 'manual_upload' %}
                  <div class=\"muted\">文件：{{ item.detail.get('filename') }}</div>
                  <div class=\"muted\">标题：{{ item.detail.get('title') }}</div>
                {% else %}
                  <div class=\"muted\">{{ item.detail.get('message', '已记录') }}</div>
                {% endif %}
              </div>
            {% endfor %}
          </div>
        </div>
      </div>
    </div>

    <div class=\"panel\" style=\"margin-top: 16px;\">
      <div class=\"section-title\">
        <h2>创建 / 修改风格预设</h2>
        <span class=\"chip\">支持覆盖</span>
      </div>
      <div class=\"layout\">
        <div class=\"card\">
          <form method=\"post\" action=\"{{ url_for('save_preset') }}\">
            <label for=\"preset_key\">预设 Key（英文/数字，唯一）</label>
            <input id=\"preset_key\" name=\"preset_key\" required placeholder=\"例如：friendly_ops\" />

            <label for=\"preset_name\">显示名称</label>
            <input id=\"preset_name\" name=\"preset_name\" required placeholder=\"例如：友好运维\" />

            <label for=\"preset_tone\">语气</label>
            <textarea id=\"preset_tone\" name=\"preset_tone\" rows=\"2\" required placeholder=\"语气描述\"></textarea>

            <label for=\"preset_structure\">结构（每行一条）</label>
            <textarea id=\"preset_structure\" name=\"preset_structure\" rows=\"3\" placeholder=\"开场感谢\\n要点列表\\n下一步行动\"></textarea>

            <label for=\"preset_closing\">结尾</label>
            <input id=\"preset_closing\" name=\"preset_closing\" required placeholder=\"例如：感谢您的配合，期待您的确认。\" />

            <label for=\"preset_reminders\">提醒（每行一条）</label>
            <textarea id=\"preset_reminders\" name=\"preset_reminders\" rows=\"3\" placeholder=\"保持句子简短\\n给出可执行指令\"></textarea>
            <button type=\"submit\">保存预设（支持覆盖）</button>
          </form>
        </div>

        <div class=\"card\">
          <div class=\"section-title\">
            <h3>当前风格预设</h3>
            <span class=\"chip\">自动合并</span>
          </div>
          <div class=\"history-list\">
            {% for key, preset in styles.items() %}
              <div class=\"preset-card\">
                <div class=\"title\">{{ key }}｜{{ preset.name }}</div>
                <div class=\"muted\">语气：{{ preset.tone }}</div>
                <div class=\"muted\">结构：{{ preset.structure|join(' / ') }}</div>
                <div class=\"muted\">提醒：{{ preset.reminders|join(' / ') }}</div>
                <div class=\"muted\">结尾：{{ preset.closing }}</div>
              </div>
            {% endfor %}
          </div>
        </div>
      </div>
    </div>
  </body>
</html>
    """
    return render_template_string(
        template,
        styles=styles,
        reply=reply,
        message=message,
        selected_styles=selected_styles,
        settings=settings,
        default_bases=DEFAULT_BASE_URLS,
        history=history,
        data_dir=DATA_DIR,
        email_value=email_value,
        priority_value=priority_value,
        attachments_text=attachments_text,
        masked_key=mask_api_key(settings.api_key),
    )


@app.route("/", methods=["GET"])
def index():
    return _render()


@app.route("/draft", methods=["POST"])
def draft():
    email = request.form.get("email", "").strip()
    if not email:
        flash("请输入邮件摘要或正文。")
        return redirect(url_for("index"))

    style_keys = request.form.getlist("style_keys") or ["concise_business"]
    priority = request.form.get("priority") or None
    attachments = _attachments_from_form(request.form.get("attachments"))

    settings = _settings_from_form(request.form)

    reply = draft_email_reply(
        email=email,
        style_keys=style_keys,
        priority=priority,
        attachments=attachments,
        data_dir=DATA_DIR,
        settings=settings,
    )

    append_history(
        DATA_DIR,
        new_entry(
            "draft",
            {
                "email": email,
                "style_keys": style_keys,
                "priority": priority,
                "attachments": attachments,
                "settings": {
                    "base_url": settings.base_url,
                    "model": settings.model,
                    "api_key": mask_api_key(settings.api_key),
                },
                "reply_preview": reply[:400],
            },
        ),
    )
    return _render(reply=reply, selected_styles=style_keys)


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
        flash("请选择要上传的 PDF 文件。"); return redirect(url_for("index"))

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".pdf"):
        flash("仅支持 PDF 文件上传。"); return redirect(url_for("index"))

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


def main() -> None:
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)


if __name__ == "__main__":
    main()
