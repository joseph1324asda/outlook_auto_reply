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
from .history import append_history, mask_api_key, new_entry
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
    style_keys: List[str]
    preset_group_ids: List[str]
    manual_kinds: List[str]


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
    raw = json.loads(path.read_text(encoding="utf-8")) or []
    sessions: List[ChatSession] = []
    for item in raw:
        messages = [ChatMessage(role=m.get("role", "user"), content=m.get("content", "")) for m in item.get("messages", [])]
        sessions.append(
            ChatSession(
                id=item.get("id", ""),
                title=item.get("title", "未命名对话"),
                messages=messages,
                style_keys=item.get("style_keys") or ["concise_business"],
                preset_group_ids=item.get("preset_group_ids") or [],
                manual_kinds=item.get("manual_kinds") or [],
            )
        )
    return sessions


def _save_chats(data_dir: Path, sessions: List[ChatSession]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _chat_file(data_dir)
    payload = []
    for session in sessions:
        payload.append(
            {
                "id": session.id,
                "title": session.title,
                "messages": [asdict(m) for m in session.messages],
                "style_keys": session.style_keys,
                "preset_group_ids": session.preset_group_ids,
                "manual_kinds": session.manual_kinds,
            }
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_chat() -> ChatSession:
    return ChatSession(
        id=uuid.uuid4().hex[:8],
        title="默认对话",
        messages=[],
        style_keys=["concise_business"],
        preset_group_ids=[],
        manual_kinds=[],
    )


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


def _preset_group_file(data_dir: Path) -> Path:
    return data_dir / "preset_groups.json"


def _load_preset_groups(data_dir: Path) -> List[Dict[str, object]]:
    path = _preset_group_file(data_dir)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")) or []
    except json.JSONDecodeError:
        return []


def _save_preset_groups(data_dir: Path, groups: List[Dict[str, object]]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _preset_group_file(data_dir)
    path.write_text(json.dumps(groups, ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_preset_groups(data_dir: Path, custom_presets: Dict[str, StylePreset]) -> List[Dict[str, object]]:
    groups = _load_preset_groups(data_dir)
    fallback_id = "ungrouped"
    fallback_name = "未分组预设"

    if not groups:
        groups = [{"id": fallback_id, "name": fallback_name, "preset_keys": list(custom_presets.keys())}]
    else:
        if not any(group.get("id") == fallback_id for group in groups):
            groups.insert(0, {"id": fallback_id, "name": fallback_name, "preset_keys": []})

    assigned = set()
    for group in groups:
        keep: List[str] = []
        for key in group.get("preset_keys", []):
            if key in custom_presets:
                keep.append(key)
                assigned.add(key)
        group["preset_keys"] = keep

    for key in custom_presets:
        if key not in assigned:
            target = next(g for g in groups if g["id"] == fallback_id)
            target.setdefault("preset_keys", []).append(key)

    _save_preset_groups(data_dir, groups)
    defaults = default_style_presets()
    merged = []
    merged.append({"id": "system", "name": "系统预设组", "presets": [{"key": k, "name": v.name} for k, v in defaults.items()]})
    for group in groups:
        presets = [{"key": key, "name": custom_presets[key].name} for key in group.get("preset_keys", []) if key in custom_presets]
        merged.append({"id": group.get("id"), "name": group.get("name", "未命名组"), "presets": presets})
    return merged


def _manual_group_file(data_dir: Path) -> Path:
    return data_dir / "manual_groups.json"


def _load_manual_group_names(data_dir: Path) -> List[str]:
    path = _manual_group_file(data_dir)
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")) or [])
    except json.JSONDecodeError:
        return []


def _save_manual_group_names(data_dir: Path, names: List[str]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _manual_group_file(data_dir)
    path.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")


def _manual_files(data_dir: Path) -> List[Tuple[Path, str]]:
    return [(data_dir / "manuals.json", "manuals"), (data_dir / "cases.json", "cases")]


def _load_manual_entries(data_dir: Path) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    for path, dataset in _manual_files(data_dir):
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) or []
        except json.JSONDecodeError:
            raw = []
        for entry in raw:
            payload = dict(entry)
            payload["dataset"] = dataset
            entries.append(payload)
    return entries


def _save_manual_dataset(data_dir: Path, dataset: str, entries: List[Dict[str, object]]) -> None:
    for path, ds in _manual_files(data_dir):
        if ds == dataset:
            path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
            return
    raise RuntimeError(f"未知说明书数据集：{dataset}")


def _manual_groups(data_dir: Path) -> Dict[str, List[str]]:
    entries = _load_manual_entries(data_dir)
    names = list(dict.fromkeys(_load_manual_group_names(data_dir)))
    for entry in entries:
        kind = str(entry.get("kind", "manual"))
        if kind not in names:
            names.append(kind)

    groups: Dict[str, List[str]] = {name: [] for name in names}
    for entry in entries:
        groups.setdefault(str(entry.get("kind", "manual")), []).append(str(entry.get("title", "未命名")))
    return groups


def _build_chat_reply(
    session: ChatSession, user_message: str, style_keys: List[str], settings: Settings, manual_kinds: List[str]
) -> str:
    style_presets = merged_presets(DATA_DIR)
    style = combine_presets(style_presets, style_keys)

    kb = KnowledgeBase.from_json_files([DATA_DIR / "manuals.json", DATA_DIR / "cases.json"])
    docs = kb.documents
    if manual_kinds:
        docs = [doc for doc in docs if doc.kind in manual_kinds]
    kb = KnowledgeBase(docs)
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


def _delete_custom_preset(data_dir: Path, key: str) -> bool:
    presets = load_custom_presets(data_dir)
    if key not in presets:
        return False

    presets_path = data_dir / "presets.json"
    remaining = {k: vars(v) for k, v in presets.items() if k != key}
    presets_path.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")

    groups = _load_preset_groups(data_dir)
    changed = False
    for group in groups:
        before = list(group.get("preset_keys", []))
        group["preset_keys"] = [k for k in before if k != key]
        if before != group["preset_keys"]:
            changed = True
    if changed:
        _save_preset_groups(data_dir, groups)
    return True


def _update_manual_entry(data_dir: Path, entry_id: str, dataset: str, title: str, kind: str) -> bool:
    for path, ds in _manual_files(data_dir):
        if ds != dataset or not path.exists():
            continue
        try:
            items = json.loads(path.read_text(encoding="utf-8")) or []
        except json.JSONDecodeError:
            items = []

        changed = False
        for item in items:
            if str(item.get("id")) == entry_id:
                item["title"] = title
                item["kind"] = kind
                changed = True
                break

        if changed:
            _save_manual_dataset(data_dir, dataset, items)
            return True
    return False


def _delete_manual_entry(data_dir: Path, entry_id: str, dataset: str) -> bool:
    for path, ds in _manual_files(data_dir):
        if ds != dataset or not path.exists():
            continue
        try:
            items = json.loads(path.read_text(encoding="utf-8")) or []
        except json.JSONDecodeError:
            items = []
        filtered = [item for item in items if str(item.get("id")) != entry_id]
        if len(filtered) != len(items):
            _save_manual_dataset(data_dir, dataset, filtered)
            return True
    return False


def _rename_manual_group(data_dir: Path, old: str, new: str) -> None:
    names = _load_manual_group_names(data_dir)
    names = [new if name == old else name for name in names]
    if new not in names:
        names.append(new)
    _save_manual_group_names(data_dir, names)

    for path, dataset in _manual_files(data_dir):
        if not path.exists():
            continue
        try:
            items = json.loads(path.read_text(encoding="utf-8")) or []
        except json.JSONDecodeError:
            items = []
        changed = False
        for item in items:
            if str(item.get("kind")) == old:
                item["kind"] = new
                changed = True
        if changed:
            _save_manual_dataset(data_dir, dataset, items)


def _delete_manual_group(data_dir: Path, target: str) -> None:
    names = [name for name in _load_manual_group_names(data_dir) if name != target]
    _save_manual_group_names(data_dir, names)

    for path, dataset in _manual_files(data_dir):
        if not path.exists():
            continue
        try:
            items = json.loads(path.read_text(encoding="utf-8")) or []
        except json.JSONDecodeError:
            items = []
        filtered = [item for item in items if str(item.get("kind")) != target]
        if len(filtered) != len(items):
            _save_manual_dataset(data_dir, dataset, filtered)


def _render(
    reply: str | None = None,
    message: str | None = None,
    selected_styles: list[str] | None = None,
    chat_id: str | None = None,
    selected_manual_groups: list[str] | None = None,
    selected_preset_groups: list[str] | None = None,
):
    styles = merged_presets(DATA_DIR)
    custom_presets = load_custom_presets(DATA_DIR)
    settings = load_saved_settings(DATA_DIR)
    sessions, active = _ensure_sessions(DATA_DIR, chat_id)
    preset_groups = _sync_preset_groups(DATA_DIR, custom_presets)
    manual_entries = _load_manual_entries(DATA_DIR)
    manual_groups = _manual_groups(DATA_DIR)
    manual_group_names = list(manual_groups.keys())

    selected_styles = selected_styles or active.style_keys or ["concise_business"]
    selected_manual_groups = selected_manual_groups or active.manual_kinds
    selected_preset_groups = selected_preset_groups or active.preset_group_ids

    template = """
<!doctype html>
<html lang="zh">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
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
        padding: 12px 18px 24px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      h1 { margin: 0; font-size: 30px; }
      h2 { margin: 0; font-size: 20px; }
      h3 { margin: 0 0 6px; font-size: 16px; }
      a { color: var(--accent-2); text-decoration: none; }
      .muted { color: var(--muted); font-size: 13px; }
      .pill { display: inline-flex; align-items: center; padding: 6px 10px; background: rgba(34,211,238,0.12); color: #a5f3fc; border-radius: 999px; font-size: 12px; gap: 6px; }
      .chip { display: inline-flex; padding: 4px 8px; border-radius: 999px; background: rgba(255,255,255,0.08); font-size: 12px; }
      .topbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; background: linear-gradient(120deg, rgba(34,211,238,0.12), rgba(139,92,246,0.12)); border: 1px solid var(--border); border-radius: 14px; padding: 12px 14px; box-shadow: 0 12px 32px rgba(0,0,0,0.28); }
      .status { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap: 8px; width: 100%; }
      .status-card { background: rgba(255,255,255,0.05); border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px; }
      .status-card strong { display: block; font-size: 12px; color: var(--muted); }
      .workspace { display: grid; grid-template-columns: 70px 270px 1fr 360px; gap: 12px; align-items: stretch; }
      .rail { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 14px; padding: 10px 6px; display: grid; gap: 10px; text-align: center; }
      .rail .icon-btn { width: 46px; height: 46px; display: grid; place-items: center; background: rgba(255,255,255,0.04); border: 1px solid var(--border); border-radius: 12px; color: var(--text); text-decoration: none; font-weight: 700; box-shadow: inset 0 1px 0 rgba(255,255,255,0.05); }
      .rail .icon-btn:hover { border-color: var(--accent-2); color: var(--accent-2); }
      .sidebar { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 12px; display: grid; grid-template-rows: auto 1fr; gap: 10px; box-shadow: 0 10px 32px rgba(0,0,0,0.32); }
      .sidebar h3 { margin-bottom: 4px; }
      .chat-list { overflow: auto; display: grid; gap: 8px; padding-right: 4px; }
      .chat-item { padding: 10px; border-radius: 10px; cursor: pointer; border: 1px solid transparent; background: rgba(255,255,255,0.02); display: grid; gap: 4px; }
      .chat-item.active { border-color: var(--accent-2); background: rgba(34,211,238,0.07); }
      .chat-panel { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 12px; display: grid; grid-template-rows: auto 1fr auto; gap: 10px; box-shadow: 0 10px 32px rgba(0,0,0,0.32); }
      .chat-header { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
      .chat-tags { display: flex; flex-wrap: wrap; gap: 6px; }
      .chat-window { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 12px; overflow-y: auto; min-height: 380px; display: grid; gap: 10px; }
      .msg { padding: 10px 12px; border-radius: 12px; max-width: 90%; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
      .msg.user { background: linear-gradient(135deg, rgba(34,211,238,0.2), rgba(34,211,238,0.08)); margin-left: auto; }
      .msg.assistant { background: rgba(255,255,255,0.05); border: 1px solid var(--border); }
      .chat-form { display: grid; gap: 8px; }
      .chat-form textarea { width: 100%; min-height: 110px; border-radius: 12px; border: 1px solid var(--border); background: rgba(255,255,255,0.04); color: var(--text); padding: 10px; font-size: 14px; resize: vertical; }
      .control-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 8px; }
      .control-card { background: rgba(255,255,255,0.04); border: 1px solid var(--border); border-radius: 12px; padding: 10px; display: grid; gap: 6px; }
      .control-card details { background: rgba(255,255,255,0.03); border-radius: 8px; padding: 8px; border: 1px solid rgba(255,255,255,0.05); }
      .control-card summary { cursor: pointer; font-weight: 700; }
      .check-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 6px; }
      .inline { display: flex; gap: 6px; align-items: center; }
      input, select, button { border-radius: 10px; border: 1px solid var(--border); background: rgba(255,255,255,0.03); color: var(--text); padding: 8px 10px; }
      button { cursor: pointer; border: none; font-weight: 700; }
      button.primary { background: linear-gradient(135deg, var(--accent-2), #2dd4bf); color: #02151f; }
      button.secondary { background: rgba(255,255,255,0.08); border: 1px solid var(--border); }
      .info-panel { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 12px; display: grid; gap: 10px; box-shadow: 0 10px 32px rgba(0,0,0,0.32); }
      .info-card { background: rgba(255,255,255,0.04); border: 1px solid var(--border); border-radius: 12px; padding: 10px; display: grid; gap: 8px; }
      .history-card { background: rgba(255,255,255,0.04); border: 1px solid var(--border); border-radius: 10px; padding: 8px; }
      .form-grid { display: grid; gap: 6px; }
      .badge-row { display: flex; flex-wrap: wrap; gap: 6px; }
      .flash { padding: 10px; border-radius: 10px; background: rgba(248,113,113,0.12); border: 1px solid rgba(248,113,113,0.35); }
    </style>
  </head>
  <body>
    <div class="topbar">
      <div>
        <div style="display:flex;align-items:center;gap:8px;">
          <h2 style="margin:0;">LLM 邮件助手</h2>
          <span class="pill">对话模式</span>
        </div>
        <div class="muted">SillyTavern 风格布局：左侧会话，中央聊天，右侧资源与库。</div>
      </div>
      <div class="status">
        <div class="status-card"><strong>数据目录</strong>{{ data_dir }}</div>
        <div class="status-card"><strong>Base</strong>{{ settings.base_url or '未设置' }}</div>
        <div class="status-card"><strong>模型</strong>{{ settings.model or '未设置' }}</div>
        <div class="status-card"><strong>API Key</strong>{{ masked_key or '未保存' }}</div>
      </div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="flash">{{ messages|join('；') }}</div>
      {% endif %}
    {% endwith %}

    {% if message %}
      <div class="flash" style="background:rgba(34,211,238,0.1);border-color:rgba(34,211,238,0.35);color:#a5f3fc;">{{ message }}</div>
    {% endif %}

    <div class="workspace">
      <div class="rail">
        <a class="icon-btn" title="会话" href="#">💬</a>
        <a class="icon-btn" title="预设" href="#presets">🎛️</a>
        <a class="icon-btn" title="说明书" href="#manuals">📚</a>
        <a class="icon-btn" title="API" href="#api">⚙️</a>
      </div>

      <div class="sidebar">
        <div>
          <h3>会话列表</h3>
          <div class="muted">切换或创建多条对话，点击进入。</div>
        </div>
        <div class="chat-list">
          <form method="post" action="{{ url_for('new_chat') }}" class="inline">
            <input name="chat_title" placeholder="新对话标题" />
            <button class="secondary" type="submit">新建</button>
          </form>
          {% for session in sessions %}
            <a class="chat-item {% if session.id == active.id %}active{% endif %}" href="{{ url_for('index', chat_id=session.id) }}">
              <div style="font-weight:700;">{{ session.title }}</div>
              <div class="muted">消息 {{ session.messages|length }}</div>
            </a>
          {% endfor %}
        </div>
      </div>

      <div class="chat-panel">
        <div class="chat-header">
          <div>
            <h3 style="margin:0;">{{ active.title }}</h3>
            <div class="muted">点击右侧折叠选择预设、说明书组与 API，底部发送消息。</div>
          </div>
          <div class="chat-tags">
            {% for key in selected_styles %}
              <span class="chip">预设 {{ key }}</span>
            {% endfor %}
            {% for gid in selected_preset_groups %}
              <span class="chip">预设组 {{ gid }}</span>
            {% endfor %}
            {% for name in selected_manual_groups %}
              <span class="chip">说明书组 {{ name }}</span>
            {% endfor %}
          </div>
        </div>

        <div class="chat-window">
          {% if not active.messages %}
            <div class="muted">暂无对话，输入内容开始交流。</div>
          {% endif %}
          {% for msg in active.messages %}
            <div class="msg {{ msg.role }}">{{ msg.content }}</div>
          {% endfor %}
        </div>

        <form class="chat-form" method="post" action="{{ url_for('send_message') }}">
          <input type="hidden" name="chat_id" value="{{ active.id }}" />
          <textarea name="message" placeholder="输入邮件需求或与助手聊天..." required>{{ last_message }}</textarea>

          <div class="control-grid">
            <div class="control-card">
              <details open>
                <summary>预设与说明书选择</summary>
                <div class="muted">勾选组快速套用，或在下方单选多选组合。</div>
                <div class="check-grid">
                  {% for group in preset_groups %}
                    <label><input type="checkbox" name="preset_group_ids" value="{{ group.id }}" {% if group.id in selected_preset_groups %}checked{% endif %}> {{ group.name }}</label>
                  {% endfor %}
                </div>
                <div class="muted">预设组合（多选覆盖）</div>
                <select name="style_keys" multiple size="4">
                  {% for key, preset in styles.items() %}
                    <option value="{{ key }}" {% if key in selected_styles %}selected{% endif %}>{{ key }}｜{{ preset.name }}</option>
                  {% endfor %}
                </select>
                <div class="muted" style="margin-top:6px;">说明书组（限制搜索范围）</div>
                <div class="check-grid">
                  {% for name in manual_group_names %}
                    <label><input type="checkbox" name="manual_groups" value="{{ name }}" {% if name in selected_manual_groups %}checked{% endif %}> {{ name }}</label>
                  {% endfor %}
                </div>
              </details>
            </div>
            <div class="control-card">
              <details open id="api-inline">
                <summary>模型与 API</summary>
                <div class="muted">可选择兼容 Base 或自定义，填写模型与 Key。</div>
                <select name="base_choice">
                  <option value="">选择兼容 Base（可选）</option>
                  {% for base in default_bases %}
                    <option value="{{ base }}" {% if settings.base_url == base %}selected{% endif %}>{{ base }}</option>
                  {% endfor %}
                </select>
                <input name="base_custom" placeholder="自定义 Base" value="{{ settings.base_url or '' }}" />
                <input name="model" placeholder="模型名称" value="{{ settings.model or '' }}" />
                <input name="api_key" type="password" placeholder="API Key" value="{{ settings.api_key or '' }}" />
                <div class="muted">示例模型：</div>
                <div class="badge-row">
                  {% for item in model_profiles %}
                    <span class="chip">{{ item.name }}｜{{ item.models|join(', ') }}</span>
                  {% endfor %}
                </div>
              </details>
            </div>
          </div>

          <div style="display:flex;justify-content:flex-end;gap:8px;">
            <button class="primary" type="submit">发送并生成回复</button>
          </div>
        </form>
      </div>

      <div class="info-panel">
        <div class="info-card" id="presets">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <h3 style="margin:0;">预设库</h3>
            <span class="chip">{{ styles|length }} 条</span>
          </div>
          <div class="muted">创建、重命名、删除预设组，或直接维护预设。</div>
          <form method="post" action="{{ url_for('save_preset') }}" class="form-grid">
            <input name="preset_key" placeholder="唯一 Key" required />
            <input name="preset_name" placeholder="显示名称" required />
            <input name="preset_tone" placeholder="语气（如：友好、专业）" />
            <input name="preset_structure" placeholder="结构（用 | 分隔段落提示）" />
            <input name="preset_closing" placeholder="结尾提示" />
            <input name="preset_reminders" placeholder="提醒（用 | 分隔）" />
            <button class="secondary" type="submit">保存/更新预设</button>
          </form>
          <form method="post" action="{{ url_for('delete_preset') }}" class="inline" style="gap:6px;">
            <input name="preset_key" placeholder="删除预设 Key" required />
            <button class="secondary" type="submit">删除自定义预设</button>
          </form>
          <details>
            <summary>管理预设组</summary>
            <div class="form-grid" style="margin-top:6px;">
              <form method="post" action="{{ url_for('manage_preset_group') }}" class="inline" style="gap:6px;">
                <input name="group_name" placeholder="新增分组名称" required />
                <input type="hidden" name="action" value="add" />
                <button class="secondary" type="submit">新增组</button>
              </form>
              <form method="post" action="{{ url_for('manage_preset_group') }}" class="inline" style="gap:6px;">
                <select name="group_id" required>
                  <option value="">选择要重命名的组</option>
                  {% for group in preset_groups %}
                    {% if group.id != 'system' %}
                      <option value="{{ group.id }}">{{ group.name }}</option>
                    {% endif %}
                  {% endfor %}
                </select>
                <input name="group_name" placeholder="新名称" required />
                <input type="hidden" name="action" value="rename" />
                <button class="secondary" type="submit">重命名组</button>
              </form>
              <form method="post" action="{{ url_for('manage_preset_group') }}" class="inline" style="gap:6px;">
                <select name="group_id" required>
                  <option value="">选择要删除的组</option>
                  {% for group in preset_groups %}
                    {% if group.id != 'system' %}
                      <option value="{{ group.id }}">{{ group.name }}</option>
                    {% endif %}
                  {% endfor %}
                </select>
                <input type="hidden" name="action" value="delete" />
                <button class="secondary" type="submit">删除组</button>
              </form>
              <form method="post" action="{{ url_for('manage_preset_group') }}" class="form-grid">
                <select name="group_id" required>
                  <option value="">选择要调整的组</option>
                  {% for group in preset_groups %}
                    {% if group.id != 'system' %}
                      <option value="{{ group.id }}">{{ group.name }}</option>
                    {% endif %}
                  {% endfor %}
                </select>
                <div class="check-grid">
                  {% for key, preset in styles.items() %}
                    <label><input type="checkbox" name="preset_keys" value="{{ key }}"> {{ preset.name }}｜{{ key }}</label>
                  {% endfor %}
                </div>
                <input type="hidden" name="action" value="assign" />
                <button class="secondary" type="submit">应用勾选预设到组</button>
              </form>
            </div>
          </details>
        </div>

        <div class="info-card" id="manuals">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <h3 style="margin:0;">说明书库</h3>
            <span class="chip">{{ manual_entries|length }} 条</span>
          </div>
          <div class="muted">折叠面板中上传、批量导入、重命名或删除说明书与分组。</div>
          <details open>
            <summary>上传/批量导入</summary>
            <div class="form-grid" style="margin-top:6px;">
              <form method="post" action="{{ url_for('upload_manual') }}" enctype="multipart/form-data" class="form-grid">
                <input name="manual_title" placeholder="标题" required />
                <input name="manual_kind" placeholder="所属分组（不存在将自动新建）" required />
                <input type="file" name="manual_pdf" accept="application/pdf" required />
                <button class="secondary" type="submit">上传 PDF</button>
              </form>
              <form method="post" action="{{ url_for('upload_manual_batch') }}" enctype="multipart/form-data" class="form-grid">
                <input name="manual_kind" placeholder="批量分组（不存在将自动新建）" required />
                <input type="file" name="manual_pdfs" accept="application/pdf" multiple />
                <div class="muted">可一次选择多个 PDF，逐个入库。</div>
                <button class="secondary" type="submit">批量上传并入库</button>
              </form>
            </div>
          </details>
          <details>
            <summary>管理分组</summary>
            <div class="form-grid" style="margin-top:6px;">
              <form method="post" action="{{ url_for('manage_manual_group') }}" class="inline" style="gap:6px;">
                <input name="group_name" placeholder="新增说明书组名称" required />
                <input type="hidden" name="action" value="add" />
                <button class="secondary" type="submit">新增组</button>
              </form>
              <form method="post" action="{{ url_for('manage_manual_group') }}" class="inline" style="gap:6px;">
                <select name="group_old" required>
                  <option value="">选择要重命名的组</option>
                  {% for name in manual_group_names %}
                    <option value="{{ name }}">{{ name }}</option>
                  {% endfor %}
                </select>
                <input name="group_new" placeholder="新名称" required />
                <input type="hidden" name="action" value="rename" />
                <button class="secondary" type="submit">重命名组</button>
              </form>
              <form method="post" action="{{ url_for('manage_manual_group') }}" class="inline" style="gap:6px;">
                <select name="group_name" required>
                  <option value="">选择要删除的组</option>
                  {% for name in manual_group_names %}
                    <option value="{{ name }}">{{ name }}</option>
                  {% endfor %}
                </select>
                <input type="hidden" name="action" value="delete" />
                <button class="secondary" type="submit">删除组</button>
              </form>
            </div>
          </details>
          <details>
            <summary>编辑/删除说明书</summary>
            <div class="form-grid" style="margin-top:6px;">
              <form method="post" action="{{ url_for('update_manual') }}" class="form-grid">
                <select name="manual_select" required>
                  <option value="">选择说明书以重命名/移动分组</option>
                  {% for item in manual_entries %}
                    <option value="{{ item.id }}||{{ item.dataset }}">{{ item.title }}｜组：{{ item.kind }}｜源：{{ item.dataset }} </option>
                  {% endfor %}
                </select>
                <input name="manual_title" placeholder="新标题" required />
                <input name="manual_kind" placeholder="分组名称（不存在将新建）" required />
                <button class="secondary" type="submit">保存说明书修改</button>
              </form>
              <form method="post" action="{{ url_for('delete_manual') }}" class="inline" style="gap:6px;">
                <select name="manual_select" required>
                  <option value="">选择要删除的说明书</option>
                  {% for item in manual_entries %}
                    <option value="{{ item.id }}||{{ item.dataset }}">{{ item.title }}（{{ item.kind }}）</option>
                  {% endfor %}
                </select>
                <button class="secondary" type="submit">删除说明书</button>
              </form>
            </div>
          </details>
        </div>

        <div class="info-card" id="api">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <h3 style="margin:0;">API 快速参考</h3>
            <span class="chip">兼容 Base</span>
          </div>
          <div class="badge-row">{% for base in default_bases %}<span class="chip">{{ base }}</span>{% endfor %}</div>
          <div class="muted">模型示例</div>
          <div class="form-grid">
            {% for item in model_profiles %}
              <div class="history-card">
                <div><strong>{{ item.name }}</strong> ｜ {{ item.base }}</div>
                <div class="muted">{{ item.models|join(', ') }}</div>
              </div>
            {% endfor %}
          </div>
          <form method="post" action="{{ url_for('save_api_settings') }}" class="form-grid">
            <input name="base_custom" placeholder="API Base" value="{{ settings.base_url or '' }}" />
            <input name="model" placeholder="模型" value="{{ settings.model or '' }}" />
            <input name="api_key" type="password" placeholder="API Key" value="{{ settings.api_key or '' }}" />
            <button class="secondary" type="submit">保存 API 信息</button>
          </form>
        </div>
      </div>
    </div>
  </body>
</html>
"""
    return render_template_string(
        template,
        data_dir=DATA_DIR,
        styles=styles,
        settings=settings,
        reply=reply,
        message=message,
        preset_groups=preset_groups,
        manual_groups=manual_groups,
        manual_entries=manual_entries,
        manual_group_names=manual_group_names,
        masked_key=mask_api_key(settings.api_key),
        default_bases=DEFAULT_BASE_URLS,
        model_profiles=MODEL_PROFILES,
        sessions=sessions,
        active=active,
        selected_styles=selected_styles,
        selected_manual_groups=selected_manual_groups,
        selected_preset_groups=selected_preset_groups,
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
    session = ChatSession(
        id=uuid.uuid4().hex[:8],
        title=title or f"会话 {len(sessions)+1}",
        messages=[],
        style_keys=["concise_business"],
        preset_group_ids=[],
        manual_kinds=[],
    )
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
    style_keys = request.form.getlist("style_keys")
    preset_group_ids = request.form.getlist("preset_group_ids")
    manual_groups = request.form.getlist("manual_groups")
    settings = _settings_from_form(request.form)

    sessions, active = _ensure_sessions(DATA_DIR, chat_id)
    if not style_keys and preset_group_ids:
        # 自动汇总所选组下的预设
        groups = _sync_preset_groups(DATA_DIR, load_custom_presets(DATA_DIR))
        found = []
        for group in groups:
            if group.get("id") in set(preset_group_ids):
                found.extend([p.get("key") for p in group.get("presets", [])])
        style_keys = found
    style_keys = style_keys or ["concise_business"]

    active.style_keys = style_keys
    active.manual_kinds = manual_groups
    active.preset_group_ids = preset_group_ids
    active.messages.append(ChatMessage(role="user", content=user_message))

    try:
        reply = _build_chat_reply(active, user_message, style_keys, settings, manual_groups)
    except Exception as exc:  # noqa: BLE001
        _save_chats(DATA_DIR, sessions)
        append_history(
            DATA_DIR,
            new_entry(
                "chat_error",
                {
                    "chat_id": active.id,
                    "message": user_message,
                    "style_keys": style_keys,
                    "error": str(exc),
                },
            ),
        )
        flash(f"回复生成失败：{exc}")
        return _render(
            selected_styles=style_keys,
            chat_id=active.id,
            selected_manual_groups=manual_groups,
            selected_preset_groups=preset_group_ids,
        )

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
                "manual_groups": manual_groups,
                "preset_group_ids": preset_group_ids,
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


@app.route("/preset/delete", methods=["POST"])
def delete_preset():
    key = (request.form.get("preset_key") or "").strip()
    if not key:
        flash("请填写要删除的预设 Key。")
        return redirect(url_for("index"))

    if key in default_style_presets():
        flash("系统预设不可删除。")
        return redirect(url_for("index"))

    if not _delete_custom_preset(DATA_DIR, key):
        flash("未找到该自定义预设。")
        return redirect(url_for("index"))

    _sync_preset_groups(DATA_DIR, load_custom_presets(DATA_DIR))
    message = f"已删除预设：{key}"
    append_history(DATA_DIR, new_entry("preset_deleted", {"key": key}))
    return _render(message=message)


@app.route("/preset/group", methods=["POST"])
def manage_preset_group():
    action = request.form.get("action") or ""
    groups = _load_preset_groups(DATA_DIR)
    fallback_id = "ungrouped"
    fallback = next((g for g in groups if g.get("id") == fallback_id), None)
    if not fallback:
        fallback = {"id": fallback_id, "name": "未分组预设", "preset_keys": []}
        groups.insert(0, fallback)

    if action == "add":
        name = (request.form.get("group_name") or "").strip()
        if not name:
            flash("请输入组名称。")
            return redirect(url_for("index"))
        if any(g.get("name") == name for g in groups):
            flash("已存在同名预设组。")
            return redirect(url_for("index"))
        groups.append({"id": uuid.uuid4().hex[:8], "name": name, "preset_keys": []})
        message = f"已新增预设组：{name}"
    elif action == "rename":
        group_id = request.form.get("group_id") or ""
        new_name = (request.form.get("group_name") or "").strip()
        target = next((g for g in groups if g.get("id") == group_id), None)
        if not target:
            flash("未找到要重命名的预设组。")
            return redirect(url_for("index"))
        target["name"] = new_name or target.get("name", "")
        message = f"已重命名预设组：{target['name']}"
    elif action == "delete":
        group_id = request.form.get("group_id") or ""
        if group_id in {"system", fallback_id}:
            flash("系统或未分组不可删除。")
            return redirect(url_for("index"))
        target = next((g for g in groups if g.get("id") == group_id), None)
        if not target:
            flash("未找到要删除的预设组。")
            return redirect(url_for("index"))
        fallback["preset_keys"].extend(target.get("preset_keys", []))
        groups = [g for g in groups if g.get("id") != group_id]
        message = "已删除预设组并保留其中预设到未分组"
    else:
        flash("未知操作。")
        return redirect(url_for("index"))

    _save_preset_groups(DATA_DIR, groups)
    _sync_preset_groups(DATA_DIR, load_custom_presets(DATA_DIR))
    return _render(message=message)


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
    group_name = (request.form.get("preset_group") or "").strip()

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
    if group_name:
        groups = _load_preset_groups(DATA_DIR)
        fallback_id = "ungrouped"
        if not any(g.get("id") == fallback_id for g in groups):
            groups.insert(0, {"id": fallback_id, "name": "未分组预设", "preset_keys": []})
        target = next((g for g in groups if g.get("name") == group_name), None)
        if not target:
            target = {"id": uuid.uuid4().hex[:8], "name": group_name, "preset_keys": []}
            groups.append(target)
        for group in groups:
            group["preset_keys"] = [k for k in group.get("preset_keys", []) if k != key]
        target.setdefault("preset_keys", []).append(key)
        _save_preset_groups(DATA_DIR, groups)
    _sync_preset_groups(DATA_DIR, load_custom_presets(DATA_DIR))
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
    files = request.files.getlist("manual_pdfs")
    files = [f for f in files if f and f.filename]
    if not files:
        flash("请选择要上传的 PDF 文件。")
        return redirect(url_for("index"))

    uploads_dir = DATA_DIR / "manual_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    messages: list[str] = []
    for file in files:
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(".pdf"):
            messages.append(f"跳过 {filename}：仅支持 PDF")
            continue

        save_path = uploads_dir / filename
        file.save(save_path)
        entry = append_manual_from_pdf(save_path, DATA_DIR)
        messages.append(f"已入库：{entry['title']}（{filename}）")
        append_history(
            DATA_DIR,
            new_entry(
                "manual_upload",
                {"filename": filename, "title": entry["title"], "source": str(save_path)},
            ),
        )

    if not messages:
        flash("未处理任何文件，请确认格式。")
        return redirect(url_for("index"))

    return _render(message="；".join(messages))


@app.route("/manual/group", methods=["POST"])
def manage_manual_group():
    action = request.form.get("action") or ""
    if action == "add":
        name = (request.form.get("group_name") or "").strip()
        if not name:
            flash("请输入说明书组名称。")
            return redirect(url_for("index"))
        names = _load_manual_group_names(DATA_DIR)
        if name not in names:
            names.append(name)
            _save_manual_group_names(DATA_DIR, names)
        message = f"已新增说明书组：{name}"
    elif action == "rename":
        old = (request.form.get("group_old") or "").strip()
        new = (request.form.get("group_new") or "").strip()
        if not old or not new:
            flash("请选择要重命名的组并填写新名称。")
            return redirect(url_for("index"))
        _rename_manual_group(DATA_DIR, old, new)
        message = f"已重命名说明书组：{old} → {new}"
    elif action == "delete":
        name = (request.form.get("group_name") or "").strip()
        if not name:
            flash("请选择要删除的组。")
            return redirect(url_for("index"))
        _delete_manual_group(DATA_DIR, name)
        message = f"已删除说明书组：{name}"
    else:
        flash("未知操作。")
        return redirect(url_for("index"))

    return _render(message=message)


@app.route("/manual/update", methods=["POST"])
def update_manual():
    raw = request.form.get("manual_select") or ""
    if "||" not in raw:
        flash("请选择需要修改的说明书。")
        return redirect(url_for("index"))
    entry_id, dataset = raw.split("||", 1)
    title = (request.form.get("manual_title") or "").strip()
    kind = (request.form.get("manual_kind") or "").strip()
    if not all([entry_id, dataset, title, kind]):
        flash("请完整填写说明书信息。")
        return redirect(url_for("index"))

    if not _update_manual_entry(DATA_DIR, entry_id, dataset, title, kind):
        flash("未找到对应的说明书，修改失败。")
        return redirect(url_for("index"))

    names = _load_manual_group_names(DATA_DIR)
    if kind not in names:
        names.append(kind)
        _save_manual_group_names(DATA_DIR, names)

    message = f"已更新说明书：{title}（组：{kind}）"
    append_history(DATA_DIR, new_entry("manual_updated", {"id": entry_id, "title": title, "kind": kind}))
    return _render(message=message)


@app.route("/manual/delete", methods=["POST"])
def delete_manual():
    raw = request.form.get("manual_select") or ""
    if "||" not in raw:
        flash("请选择需要删除的说明书。")
        return redirect(url_for("index"))
    entry_id, dataset = raw.split("||", 1)

    if not _delete_manual_entry(DATA_DIR, entry_id, dataset):
        flash("未找到对应的说明书。")
        return redirect(url_for("index"))

    append_history(DATA_DIR, new_entry("manual_deleted", {"id": entry_id, "dataset": dataset}))
    return _render(message="已删除说明书")


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
