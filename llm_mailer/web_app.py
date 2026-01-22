from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Dict, List, Tuple

from flask import Flask, flash, redirect, render_template_string, request, url_for
from werkzeug.utils import secure_filename

from .ai_client import get_provider
from .config import Settings, load_saved_settings, save_settings
from .history import append_history, mask_api_key, new_entry
from .ingestion import append_manual_from_pdf
from .knowledge_base import KnowledgeBase
from .prompt_builder import PromptBuilder, PromptContext
from .styles import (
    PresetEntry,
    StylePreset,
    combine_entries,
    delete_preset_entry,
    load_custom_presets,
    save_custom_presets,
    upsert_preset_entry,
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
    preset_group_ids: List[str]
    manual_kinds: List[str]


def _parse_float(value: str | None, default: float | None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_int(value: str | None, default: int | None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _settings_from_form(form) -> Settings:
    saved = load_saved_settings(DATA_DIR)
    base_choice = form.get("base_choice") or None
    base_custom = form.get("base_custom") or None
    base_url = base_custom or base_choice or saved.base_url

    api_key = form.get("api_key") or saved.api_key
    model = form.get("model") or saved.model
    temperature = _parse_float(form.get("temperature"), saved.temperature)
    top_p = _parse_float(form.get("top_p"), saved.top_p)
    top_k = _parse_int(form.get("top_k"), saved.top_k)
    settings = Settings(
        api_key=api_key,
        model=model,
        base_url=base_url,
        temperature=temperature,
        top_p=top_p,
        top_k=top_k,
    )
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


def _sync_preset_groups(data_dir: Path, custom_presets: Dict[str, PresetEntry]) -> List[Dict[str, object]]:
    groups = _load_preset_groups(data_dir)
    fallback_id = "ungrouped"
    fallback_name = "未分组预设"

    grouped_entry_content: Dict[str, List[str]] = {}
    for entry in custom_presets.values():
        grouped_entry_content.setdefault(entry.group_id, []).append(entry.content)

    normalized: List[Dict[str, object]] = []
    seen = set()
    for group in groups:
        gid = group.get("id") or uuid.uuid4().hex[:8]
        name = group.get("name") or "未命名组"
        prompt = group.get("prompt") or "\n\n".join(grouped_entry_content.get(gid, []))
        if gid in seen:
            gid = uuid.uuid4().hex[:8]
        seen.add(gid)
        normalized.append({"id": gid, "name": name, "prompt": prompt or ""})

    if not any(g.get("id") == fallback_id for g in normalized):
        normalized.insert(0, {"id": fallback_id, "name": fallback_name, "prompt": ""})

    available_group_ids = {g["id"] for g in normalized}
    presets_changed = False
    updated_presets: Dict[str, PresetEntry] = {}
    for entry in custom_presets.values():
        target_group = entry.group_id if entry.group_id in available_group_ids else fallback_id
        if target_group != entry.group_id:
            presets_changed = True
            updated_presets[entry.id] = replace(entry, group_id=target_group)
        else:
            updated_presets[entry.id] = entry

    if presets_changed:
        from .styles import save_custom_presets

        save_custom_presets(data_dir, updated_presets)
        custom_presets = updated_presets

    _save_preset_groups(data_dir, normalized)
    return normalized


def _style_from_group_ids(group_ids: List[str], custom_presets: Dict[str, PresetEntry]) -> StylePreset:
    entries = [entry for entry in custom_presets.values() if entry.group_id in group_ids]
    return combine_entries(entries)


def _entries_from_groups(groups: List[Dict[str, object]]) -> List[PresetEntry]:
    entries: List[PresetEntry] = []
    for group in groups:
        content = (group.get("prompt") or "").strip()
        if not content:
            continue
        entries.append(
            PresetEntry(
                id=group.get("id", uuid.uuid4().hex[:8]),
                name=group.get("name", "未命名组"),
                content=content,
                group_id=group.get("id", ""),
            )
        )
    return entries


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
    session: ChatSession, user_message: str, style_entries: List[PresetEntry], settings: Settings, manual_kinds: List[str]
) -> str:
    style = combine_entries(style_entries)

    kb = KnowledgeBase.from_json_files([DATA_DIR / "manuals.json", DATA_DIR / "cases.json"])
    docs = kb.documents
    if manual_kinds:
        docs = [doc for doc in docs if doc.kind in manual_kinds]
    kb = KnowledgeBase(docs)
    references = kb.search(user_message, limit=4)

    history_text = "\n".join([f"{m.role}: {m.content}" for m in session.messages[-6:]]) or "(no history)"
    refs_text = "\n".join([f"- {doc.title}｜{doc.kind}" for doc in references]) or "- no matching documents"

    prompt = f"""
You are a conversation assistant. Respond in a friendly, concise English tone.
Current style guide: {style.name}
Style hints: {style.content}

Recent messages:
{history_text}

Relevant manuals:
{refs_text}

User message: {user_message}
Provide the next reply in English and cite any helpful manual points.
"""
    provider = get_provider(settings)
    print(
        "[AI REQUEST] model=%s base=%s temperature=%s top_p=%s top_k=%s"
        % (
            settings.model,
            settings.base_url or "https://api.openai.com/v1",
            settings.temperature,
            settings.top_p,
            settings.top_k,
        )
    )
    print("[AI PROMPT]", prompt)
    return provider.generate(prompt)


def _build_outlook_reply(
    subject: str,
    body: str,
    preset_group_ids: List[str],
    settings: Settings,
) -> str:
    custom_presets = load_custom_presets(DATA_DIR)
    style = _style_from_group_ids(preset_group_ids, custom_presets)

    kb = KnowledgeBase.from_json_files([DATA_DIR / "manuals.json", DATA_DIR / "cases.json"])
    outlook_context = f"Subject: {subject or '(no subject)'}\n\nBody:\n{body or '(no body provided)'}"
    references = kb.search(outlook_context, limit=5)
    prompt = PromptBuilder(style).build(PromptContext(email_summary=outlook_context), references)

    provider = get_provider(settings)
    print(
        "[AI REQUEST][Outlook] model=%s base=%s temperature=%s top_p=%s top_k=%s",
        settings.model,
        settings.base_url or "https://api.openai.com/v1",
        settings.temperature,
        settings.top_p,
        settings.top_k,
    )
    print("[AI PROMPT][Outlook]", prompt)
    return provider.generate(prompt)


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
    chat_id: str | None = None,
    selected_manual_groups: list[str] | None = None,
    selected_preset_groups: list[str] | None = None,
):
    custom_presets = load_custom_presets(DATA_DIR)
    entries_by_group: dict[str, list[PresetEntry]] = {}
    for entry in custom_presets.values():
        entries_by_group.setdefault(entry.group_id, []).append(entry)
    settings = load_saved_settings(DATA_DIR)
    sessions, active = _ensure_sessions(DATA_DIR, chat_id)
    preset_groups = _sync_preset_groups(DATA_DIR, custom_presets)
    manual_entries = _load_manual_entries(DATA_DIR)
    manual_groups = _manual_groups(DATA_DIR)
    manual_group_names = list(manual_groups.keys())

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
        --accent: #22d3ee;
        --accent-2: #8b5cf6;
        --danger: #f87171;
      }
      * { box-sizing: border-box; }
      body {
        font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
        background: radial-gradient(circle at 20% 18%, rgba(34,211,238,0.08), transparent 22%),
                    radial-gradient(circle at 82% 8%, rgba(139,92,246,0.1), transparent 24%),
                    var(--bg);
        color: var(--text);
        margin: 0;
        min-height: 100vh;
        padding: 14px 18px 22px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      h2 { margin: 0; font-size: 22px; }
      h3 { margin: 0; font-size: 16px; }
      .muted { color: var(--muted); font-size: 13px; }
      .top-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }
      .card { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 12px; box-shadow: 0 10px 26px rgba(0,0,0,0.32); display: grid; gap: 8px; }
      .layout { display: grid; grid-template-columns: 260px 1fr 340px; gap: 12px; align-items: stretch; }
      .sidebar { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 10px; display: grid; gap: 10px; grid-template-rows: auto 1fr; }
      .chat-list { overflow-y: auto; display: grid; gap: 8px; padding-right: 4px; }
      .chat-item { padding: 10px; border-radius: 10px; border: 1px solid transparent; background: rgba(255,255,255,0.02); display: grid; gap: 4px; position: relative; }
      .chat-item.active { border-color: var(--accent); background: rgba(34,211,238,0.08); }
      .chat-item .chat-delete { position: absolute; top: 6px; right: 6px; }
      .chat-item .icon-btn { border: none; background: rgba(248,113,113,0.1); color: #fca5a5; width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font-weight: 900; cursor: pointer; }
      .chat-item .chat-link { color: inherit; text-decoration: none; display: grid; gap: 4px; }
      .chat-shell { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 12px; display: grid; grid-template-rows: auto auto 1fr auto; gap: 10px; height: 640px; }
      .chat-header { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
      .tag-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
      .chip { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 999px; background: rgba(255,255,255,0.06); border: 1px solid var(--border); font-size: 12px; }
      .chip small { color: var(--muted); }
      .chip-check { display: inline-flex; align-items: center; }
      .chip-check input { display: none; }
      .chip-check span { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 999px; border: 1px solid var(--border); background: rgba(255,255,255,0.05); cursor: pointer; }
      .chip-check input:checked + span { border-color: var(--accent); background: rgba(34,211,238,0.1); }
      .chat-window { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 12px; overflow-y: auto; height: 380px; display: grid; gap: 10px; }
      .msg { padding: 10px 12px; border-radius: 12px; max-width: 90%; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
      .msg.user { background: linear-gradient(135deg, rgba(34,211,238,0.18), rgba(34,211,238,0.08)); margin-left: auto; }
      .msg.assistant { background: rgba(255,255,255,0.05); border: 1px solid var(--border); }
      .chat-form { display: grid; gap: 8px; }
      textarea, input, select, button { border-radius: 10px; border: 1px solid var(--border); background: rgba(255,255,255,0.03); color: var(--text); padding: 8px 10px; font-size: 14px; }
      textarea { width: 100%; min-height: 120px; resize: vertical; }
      button { cursor: pointer; font-weight: 700; }
      button.primary { background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: #0b0f1a; }
      button.secondary { background: rgba(255,255,255,0.08); }
      .preset-panel { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; padding: 12px; display: grid; gap: 10px; }
      .preset-list { display: grid; gap: 10px; }
      .preset-group-card { border: 1px solid var(--border); border-radius: 12px; padding: 10px; background: rgba(255,255,255,0.02); display: grid; gap: 8px; }
      .entry-scroll { max-height: 220px; overflow-y: auto; display: grid; gap: 8px; padding-right: 4px; }
      .entry-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
      .entry-chip { display: inline-flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 10px; background: rgba(255,255,255,0.06); border: 1px solid var(--border); }
      .entry-actions { display: flex; gap: 6px; align-items: center; }
      .icon-btn { border: none; background: rgba(248,113,113,0.1); color: #fca5a5; width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font-weight: 900; cursor: pointer; }
      .mini-modal summary { display: inline-flex; align-items: center; gap: 6px; }
      .form-grid { display: grid; gap: 6px; }
      .check-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 6px; }
      details { border: 1px solid var(--border); border-radius: 10px; padding: 8px; background: rgba(255,255,255,0.03); }
      summary { cursor: pointer; font-weight: 700; }
      details.modal-card { border-style: dashed; background: rgba(255,255,255,0.02); }
      details.modal-card[open] { box-shadow: 0 12px 26px rgba(0,0,0,0.28); border-color: var(--accent); }
      details.modal-card .modal-body { margin-top: 6px; display: grid; gap: 6px; }
      .flash { padding: 10px; border-radius: 10px; background: rgba(248,113,113,0.12); border: 1px solid rgba(248,113,113,0.35); }
    </style>
  </head>
  <body>
    <div class="top-row">
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <h2>API / 说明概览</h2>
          <span class="chip">Model: {{ settings.model or 'unset' }} ｜ Temp {{ settings.temperature if settings.temperature is not none else 'auto' }} ｜ Top-p {{ settings.top_p if settings.top_p is not none else 'auto' }} ｜ Top-k {{ settings.top_k if settings.top_k is not none else 'auto' }}</span>
        </div>
        <div class="muted">Keep all API, model, and manual actions up top so the chat stays focused. Use the randomness controls to tune creativity.</div>
        <form method="post" action="{{ url_for('save_api_settings') }}" class="form-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px,1fr));">
          <input name="base_custom" placeholder="API Base" value="{{ settings.base_url or '' }}" />
          <input name="model" placeholder="Model" value="{{ settings.model or '' }}" />
          <input name="api_key" type="password" placeholder="API Key" value="{{ settings.api_key or '' }}" />
          <input name="temperature" type="number" step="0.05" min="0" max="2" placeholder="Temperature" value="{{ settings.temperature if settings.temperature is not none else '' }}" />
          <input name="top_p" type="number" step="0.05" min="0" max="1" placeholder="Top-p" value="{{ settings.top_p if settings.top_p is not none else '' }}" />
          <input name="top_k" type="number" step="1" min="1" placeholder="Top-k" value="{{ settings.top_k if settings.top_k is not none else '' }}" />
          <button class="secondary" type="submit">保存 / Save API</button>
        </form>
        <div class="tag-row">
          {% for base in default_bases %}<span class="chip">{{ base }}</span>{% endfor %}
        </div>
        <div class="check-grid">
          {% for item in model_profiles %}
            <div class="chip">{{ item.name }} ｜ {{ item.models|join(', ') }}<small>{{ item.base }}</small></div>
          {% endfor %}
        </div>
      </div>
      <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <h2>说明书库</h2>
          <span class="chip">{{ manual_entries|length }} 条</span>
        </div>
        <details open>
          <summary>上传/批量导入</summary>
          <div class="form-grid" style="margin-top:6px;grid-template-columns: repeat(auto-fit, minmax(220px,1fr));">
            <form method="post" action="{{ url_for('upload_manual') }}" enctype="multipart/form-data" class="form-grid">
              <input name="manual_title" placeholder="标题" required />
              <input name="manual_kind" placeholder="分组" required />
              <input type="file" name="manual_pdf" accept="application/pdf" required />
              <button class="secondary" type="submit">上传 PDF</button>
            </form>
            <form method="post" action="{{ url_for('upload_manual_batch') }}" enctype="multipart/form-data" class="form-grid">
              <input name="manual_kind" placeholder="批量分组" required />
              <input type="file" name="manual_pdfs" accept="application/pdf" multiple />
              <div class="muted">支持一次性选择多个文件</div>
              <button class="secondary" type="submit">批量上传</button>
            </form>
          </div>
        </details>
        <details>
          <summary>管理分组</summary>
          <div class="form-grid" style="margin-top:6px;">
            <form method="post" action="{{ url_for('manage_manual_group') }}" class="form-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px,1fr));">
              <input name="group_name" placeholder="新增组" required />
              <input type="hidden" name="action" value="add" />
              <button class="secondary" type="submit">新增</button>
            </form>
            <form method="post" action="{{ url_for('manage_manual_group') }}" class="form-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px,1fr));">
              <select name="group_old" required>
                <option value="">选择组</option>
                {% for name in manual_group_names %}<option value="{{ name }}">{{ name }}</option>{% endfor %}
              </select>
              <input name="group_new" placeholder="新名称" required />
              <input type="hidden" name="action" value="rename" />
              <button class="secondary" type="submit">重命名</button>
            </form>
            <form method="post" action="{{ url_for('manage_manual_group') }}" class="form-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px,1fr));">
              <select name="group_name" required>
                <option value="">选择要删除的组</option>
                {% for name in manual_group_names %}<option value="{{ name }}">{{ name }}</option>{% endfor %}
              </select>
              <input type="hidden" name="action" value="delete" />
              <button class="secondary" type="submit">删除</button>
            </form>
          </div>
        </details>
        <details>
          <summary>编辑/删除说明书</summary>
          <div class="form-grid" style="margin-top:6px;">
            <form method="post" action="{{ url_for('update_manual') }}" class="form-grid">
              <select name="manual_select" required>
                <option value="">选择说明书</option>
                {% for item in manual_entries %}
                  <option value="{{ item.id }}||{{ item.dataset }}">{{ item.title }}｜{{ item.kind }}</option>
                {% endfor %}
              </select>
              <input name="manual_title" placeholder="新标题" required />
              <input name="manual_kind" placeholder="分组" required />
              <button class="secondary" type="submit">保存修改</button>
            </form>
            <form method="post" action="{{ url_for('delete_manual') }}" class="form-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px,1fr));">
              <select name="manual_select" required>
                <option value="">选择说明书</option>
                {% for item in manual_entries %}
                  <option value="{{ item.id }}||{{ item.dataset }}">{{ item.title }}（{{ item.kind }}）</option>
                {% endfor %}
              </select>
              <button class="secondary" type="submit">删除</button>
            </form>
          </div>
        </details>
      </div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}<div class="flash">{{ messages[0] }}</div>{% endif %}
    {% endwith %}
    {% if message %}<div class="flash">{{ message }}</div>{% endif %}

    <div class="layout">
      <aside class="sidebar">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
          <h3>会话</h3>
          <form method="post" action="{{ url_for('new_chat') }}" style="display:flex;gap:6px;">
            <input name="chat_title" placeholder="新标题" />
            <button class="secondary" type="submit">新增</button>
          </form>
        </div>
          <div class="chat-list">
          {% for session in sessions %}
            <div class="chat-item {% if session.id == active.id %}active{% endif %}">
              <a class="chat-link" href="{{ url_for('index', chat_id=session.id) }}">
                <div><strong>{{ session.title }}</strong></div>
                <div class="muted">{{ session.messages|length }} 条</div>
              </a>
              <form class="chat-delete" method="post" action="{{ url_for('delete_chat') }}" onsubmit="return confirm('确定删除此对话吗？');">
                <input type="hidden" name="chat_id" value="{{ session.id }}" />
                <button class="icon-btn" type="submit">✕</button>
              </form>
            </div>
          {% endfor %}
        </div>
      </aside>

        <section class="chat-shell">
          <div class="chat-header">
            <div>
              <h3>{{ active.title }}</h3>
              <div class="muted">聊天内容居中展示，底部输入发送。</div>
            </div>
            <div class="tag-row">
              {% for gid in selected_preset_groups %}
                {% set group = (preset_groups | selectattr('id', 'equalto', gid) | list | first) %}
                {% if group %}<span class="chip">{{ group.name }}<small>预设组</small></span>{% endif %}
              {% endfor %}
              {% for name in selected_manual_groups %}<span class="chip">{{ name }}</span>{% endfor %}
            </div>
          </div>

        <div class="chat-window">
          {% if not active.messages %}<div class="muted">暂无消息，先选好预设与说明书后发送吧。</div>{% endif %}
          {% for msg in active.messages %}
            <div class="msg {{ msg.role }}">{{ msg.content }}</div>
          {% endfor %}
        </div>

        <form class="chat-form" method="post" action="{{ url_for('send_message') }}" enctype="multipart/form-data">
          <input type="hidden" name="chat_id" value="{{ active.id }}" />
          <div class="tag-row">
            {% for group in preset_groups %}
              <label class="chip-check">
                <input type="checkbox" name="preset_group_ids" value="{{ group.id }}" {% if group.id in selected_preset_groups %}checked{% endif %}>
                <span>{{ group.name }}<small>组</small></span>
              </label>
            {% endfor %}
          </div>
          <div class="tag-row">
            {% for name, items in manual_groups.items() %}
              <label class="chip-check">
                <input type="checkbox" name="manual_groups" value="{{ name }}" {% if name in selected_manual_groups %}checked{% endif %}>
                <span>{{ name }}<small>{{ items|length }} 条</small></span>
              </label>
            {% endfor %}
          </div>
          <textarea name="message" placeholder="输入要发送的内容" required></textarea>
          <div style="display:flex;gap:8px;justify-content:flex-end;">
            <button class="secondary" type="reset">清空</button>
            <button class="primary" type="submit">发送</button>
          </div>
        </form>
      </section>

      <section class="preset-panel">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <h3>预设组</h3>
          <span class="chip">{{ preset_groups|length }} 组</span>
        </div>
        <div class="muted">每个预设组可以包含多条提示，支持在此增加、修改、删除；条目区域固定高度，可用滚轮浏览。</div>
        <details class="modal-card" open>
          <summary>新增预设组</summary>
          <div class="modal-body">
            <form method="post" action="{{ url_for('manage_preset_group') }}" class="form-grid">
              <input name="group_name" placeholder="组名称" required />
              <input type="hidden" name="action" value="add" />
              <button class="secondary" type="submit">创建预设组</button>
            </form>
          </div>
        </details>
        <details class="modal-card" open>
          <summary>重命名 / 删除分组</summary>
          <div class="modal-body" style="display:grid;gap:10px;">
            {% for group in preset_groups %}
              <div class="preset-group-card">
                <form method="post" action="{{ url_for('manage_preset_group') }}" class="form-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px,1fr));">
                  <input type="hidden" name="group_id" value="{{ group.id }}" />
                  <input name="group_name" value="{{ group.name }}" placeholder="组名称" required />
                  <input type="hidden" name="action" value="update" />
                  <div style="display:flex;gap:8px;justify-content:flex-end;grid-column:1/-1;">
                    <button class="secondary" type="submit">保存</button>
                    {% if group.id != 'ungrouped' %}
                      <button class="secondary" type="submit" name="action" value="delete" onclick="return confirm('删除该预设组？');">删除</button>
                    {% endif %}
                  </div>
                </form>
                <div class="entry-scroll">
                  {% set entries = entries_by_group.get(group.id, []) %}
                  {% if not entries %}
                    <div class="muted">该组暂无条目，可下方添加。</div>
                  {% endif %}
                  {% for entry in entries %}
                    <div class="entry-row">
                      <span class="entry-chip">{{ entry.name }}</span>
                      <div class="entry-actions">
                        <details class="modal-card mini-modal">
                          <summary class="chip">编辑</summary>
                          <div class="modal-body form-grid">
                            <form method="post" action="{{ url_for('save_preset') }}" class="form-grid">
                              <input type="hidden" name="entry_id" value="{{ entry.id }}" />
                              <input type="hidden" name="preset_group_id" value="{{ group.id }}" />
                              <input name="preset_name" value="{{ entry.name }}" placeholder="条目名称" required />
                              <textarea name="preset_content" placeholder="预设内容" style="min-height:120px;">{{ entry.content }}</textarea>
                              <button class="secondary" type="submit">保存</button>
                            </form>
                          </div>
                        </details>
                        <form method="post" action="{{ url_for('delete_preset') }}">
                          <input type="hidden" name="entry_id" value="{{ entry.id }}" />
                          <button class="icon-btn" type="submit" onclick="return confirm('删除该条目？');">×</button>
                        </form>
                      </div>
                    </div>
                  {% endfor %}
                </div>
                <details class="modal-card mini-modal" open>
                  <summary class="chip">新增条目</summary>
                  <div class="modal-body">
                    <form method="post" action="{{ url_for('save_preset') }}" class="form-grid">
                      <input type="hidden" name="preset_group_id" value="{{ group.id }}" />
                      <input name="preset_name" placeholder="条目名称" required />
                      <textarea name="preset_content" placeholder="预设内容" style="min-height:120px;"></textarea>
                      <button class="secondary" type="submit">保存条目</button>
                    </form>
                  </div>
                </details>
              </div>
            {% endfor %}
          </div>
        </details>
      </section>
    </div>
  </body>
</html>

"""

    return render_template_string(
        template,
        data_dir=DATA_DIR,
        settings=settings,
        reply=reply,
        message=message,
        preset_groups=preset_groups,
        preset_entries=list(custom_presets.values()),
        entries_by_group=entries_by_group,
        manual_groups=manual_groups,
        manual_entries=manual_entries,
        manual_group_names=manual_group_names,
        masked_key=mask_api_key(settings.api_key),
        default_bases=DEFAULT_BASE_URLS,
        model_profiles=MODEL_PROFILES,
        sessions=sessions,
        active=active,
        selected_manual_groups=selected_manual_groups,
        selected_preset_groups=selected_preset_groups,
        last_message="",
    )


def _render_outlook(
    reply: str | None = None,
    error: str | None = None,
    subject: str = "",
    body: str = "",
    selected_groups: list[str] | None = None,
):
    custom_presets = load_custom_presets(DATA_DIR)
    preset_groups = _sync_preset_groups(DATA_DIR, custom_presets)
    settings = load_saved_settings(DATA_DIR)
    selected_groups = selected_groups or []

    template = """
<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Outlook Auto Reply</title>
    <style>
      :root {
        --bg: #0b1220;
        --panel: #0f172a;
        --card: #111827;
        --border: #1f2937;
        --text: #e5e7eb;
        --muted: #9ca3af;
        --accent: #22d3ee;
      }
      * { box-sizing: border-box; }
      body { margin: 0; background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; padding: 14px; display: grid; gap: 12px; }
      .shell { max-width: 1100px; margin: 0 auto; display: grid; gap: 12px; }
      .card { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 12px; box-shadow: 0 12px 26px rgba(0,0,0,0.26); }
      h1 { margin: 0 0 6px 0; font-size: 20px; }
      .muted { color: var(--muted); font-size: 13px; }
      label { font-weight: 600; font-size: 13px; }
      textarea, input, button { width: 100%; border-radius: 10px; border: 1px solid var(--border); background: rgba(255,255,255,0.03); color: var(--text); padding: 10px; font-size: 14px; }
      textarea { min-height: 90px; resize: vertical; }
      button { cursor: pointer; font-weight: 700; background: linear-gradient(135deg, var(--accent), #3b82f6); color: #0b0f1a; border: none; }
      .inline { display: flex; gap: 10px; align-items: center; justify-content: space-between; }
      .chip-list { display: flex; flex-wrap: wrap; gap: 8px; padding: 6px 0; }
      .chip { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border-radius: 999px; border: 1px solid var(--border); background: rgba(255,255,255,0.06); cursor: pointer; font-size: 13px; }
      .chip input { display: none; }
      .chip span { display: inline-flex; align-items: center; gap: 6px; }
      .pill-checked { background: rgba(34,211,238,0.18); border-color: var(--accent); }
      .grid { display: grid; gap: 10px; }
      .reply-box { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px; min-height: 160px; white-space: pre-wrap; }
      .error { border: 1px solid rgba(248,113,113,0.65); background: rgba(248,113,113,0.12); color: #fecaca; border-radius: 10px; padding: 10px; }
      .top-actions { display: flex; gap: 8px; align-items: center; justify-content: space-between; flex-wrap: wrap; }
      .top-actions .muted { text-align: right; }
    </style>
    <script src=\"https://appsforoffice.microsoft.com/lib/1/hosted/office.js\"></script>
  </head>
  <body>
    <div class=\"shell\">
      <div class=\"top-actions\">
        <div>
          <h1>Outlook preset reply</h1>
          <div class=\"muted\">Select preset groups and click once to generate. Use the settings pop-out to update API keys or manuals.</div>
        </div>
        <div class=\"muted\">Model: {{ settings.model or 'unset' }} ｜ Base: {{ settings.base_url or 'unset' }}</div>
      </div>
      <button style=\"width:auto;justify-self:flex-start;\" onclick=\"window.open('{{ url_for('outlook_settings') }}','settings','width=960,height=780');\">Open settings pop-out</button>
      <form method=\"post\" action=\"{{ url_for('outlook_generate') }}\" class=\"card grid\">
        <div class=\"grid\">
          <label for=\"preset_groups\">Preset groups</label>
          <div class=\"chip-list\">
            {% for group in preset_groups %}
              <label class=\"chip {{ 'pill-checked' if group.id in selected_groups else '' }}\">
                <input type=\"checkbox\" name=\"preset_groups\" value=\"{{ group.id }}\" {% if group.id in selected_groups %}checked{% endif %} />
                <span>{{ group.name }}</span>
              </label>
            {% endfor %}
            {% if not preset_groups %}<span class=\"muted\">No preset groups yet.</span>{% endif %}
          </div>
        </div>
        <div class=\"grid\">
          <label for=\"subject\">Email subject</label>
          <input id=\"subject\" name=\"subject\" placeholder=\"Subject from Outlook...\" value=\"{{ subject }}\" />
        </div>
        <div class=\"grid\">
          <label for=\"body\">Email body</label>
          <textarea id=\"body\" name=\"body\" placeholder=\"Body from Outlook or paste content here...\">{{ body }}</textarea>
        </div>
        <div class=\"inline\">
          <div class=\"muted\">Outlook-facing form keeps only preset selection and one-click generation.</div>
          <button type=\"submit\" style=\"max-width:220px;\">Generate reply</button>
        </div>
      </form>
      {% if error %}<div class=\"error\">{{ error }}</div>{% endif %}
      <div class=\"card\">
        <div class=\"inline\" style=\"align-items:center;\">
          <div class=\"muted\">Generated reply</div>
          <button type=\"button\" id=\"insert-reply\" style=\"max-width:200px;\">Insert into Outlook</button>
        </div>
        <div class=\"reply-box\">{{ reply or 'Pending generation...' }}</div>
      </div>
    </div>
    <script>
      const subjectField = document.getElementById('subject');
      const bodyField = document.getElementById('body');
      const replyText = {{ (reply or '')|tojson }};
      const insertButton = document.getElementById('insert-reply');
      const maybeFillSubject = (value) => {
        if (value && !subjectField.value) {
          subjectField.value = value;
        }
      };
      const extractFirstMessageBody = (value) => {
        if (!value) {
          return '';
        }
        const markers = [
          /^\s*-{2,}\s*Original Message\s*-{2,}\s*$/m,
          /^\s*-{2,}\s*原始邮件\s*-{2,}\s*$/m,
          /^\s*On .* wrote:\s*$/m,
          /^\s*From:\s*/m,
          /^\s*Sent:\s*/m,
          /^\s*To:\s*/m,
          /^\s*Cc:\s*/m,
          /^\s*Subject:\s*/m,
          /^\s*发件人:\s*/m,
          /^\s*发送时间:\s*/m,
          /^\s*收件人:\s*/m,
          /^\s*抄送:\s*/m,
          /^\s*主题:\s*/m,
          /^\s*时间:\s*/m,
          /^\s*邮件号:\s*/m,
        ];
        let cutIndex = value.length;
        for (const marker of markers) {
          const match = value.match(marker);
          if (match && match.index !== undefined) {
            cutIndex = Math.min(cutIndex, match.index);
          }
        }
        return value.slice(0, cutIndex).trim();
      };
      const maybeFillBody = (value) => {
        if (value && !bodyField.value.trim()) {
          const cleaned = extractFirstMessageBody(value);
          bodyField.value = cleaned || value;
        }
      };
      const toHtml = (value) => {
        if (!value) {
          return '';
        }
        const escaped = value
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;');
        return escaped.replace(/\n/g, '<br>');
      };
      const insertReplyIntoOutlook = () => {
        if (!replyText || !window.Office || !Office.context || !Office.context.mailbox) {
          return;
        }
        const item = Office.context.mailbox.item;
        if (item && item.body && item.body.setAsync) {
          const html = toHtml(replyText);
          item.body.setAsync(html, { coercionType: Office.CoercionType.Html }, function(res) {
            if (res.status !== Office.AsyncResultStatus.Succeeded) {
              console.warn('Failed to insert reply into Outlook.', res.error);
            }
          });
        }
      };
      if (insertButton) {
        insertButton.addEventListener('click', insertReplyIntoOutlook);
      }
      if (window.Office && Office.onReady) {
        Office.onReady(function(info) {
          try {
            const item = Office.context && Office.context.mailbox && Office.context.mailbox.item;
            if (!item) { return; }
            if (item.subject) {
              if (typeof item.subject === 'string') {
                maybeFillSubject(item.subject);
              } else if (item.subject.getAsync) {
                item.subject.getAsync(function(res) {
                  if (res.status === Office.AsyncResultStatus.Succeeded) {
                    maybeFillSubject(res.value || '');
                  }
                });
              }
            }
            if (item.getReplyBodyAsync) {
              item.getReplyBodyAsync(function(res) {
                if (res.status === Office.AsyncResultStatus.Succeeded) {
                  maybeFillBody(res.value || '');
                }
              });
            } else if (item.body && item.body.getAsync) {
              item.body.getAsync('text', { asyncContext: null }, function(res) {
                if (res.status === Office.AsyncResultStatus.Succeeded) {
                  maybeFillBody(res.value || '');
                }
              });
            }
          } catch (err) {
            console.warn('Office context unavailable', err);
          }
        });
      }
    </script>
  </body>
</html>
"""

    return render_template_string(
        template,
        preset_groups=preset_groups,
        reply=reply,
        error=error,
        subject=subject,
        body=body,
        selected_groups=selected_groups,
        settings=settings,
    )


@app.route("/outlook", methods=["GET"])
def outlook_home():
    return _render_outlook()


@app.route("/outlook/generate", methods=["POST"])
def outlook_generate():
    subject = (request.form.get("subject") or "").strip()
    body = (request.form.get("body") or "").strip()
    preset_group_ids = request.form.getlist("preset_groups")
    settings = _settings_from_form(request.form)

    try:
        reply = _build_outlook_reply(subject, body, preset_group_ids, settings)
    except Exception as exc:  # noqa: BLE001
        append_history(
            DATA_DIR,
            new_entry(
                "outlook_error",
                {
                    "subject": subject,
                    "preset_groups": preset_group_ids,
                    "error": str(exc),
                },
            ),
        )
        return _render_outlook(
            error=str(exc),
            subject=subject,
            body=body,
            selected_groups=preset_group_ids,
        )

    append_history(
        DATA_DIR,
        new_entry(
            "outlook_generated",
            {
                "subject": subject,
                "preset_groups": preset_group_ids,
                "settings": {
                    "base_url": settings.base_url,
                    "model": settings.model,
                    "api_key": mask_api_key(settings.api_key),
                },
                "reply_preview": reply[:200],
            },
        ),
    )
    return _render_outlook(
        reply=reply,
        subject=subject,
        body=body,
        selected_groups=preset_group_ids,
    )


@app.route("/outlook/settings", methods=["GET"])
def outlook_settings():
    settings = load_saved_settings(DATA_DIR)
    manual_entries = _load_manual_entries(DATA_DIR)
    preset_groups = _sync_preset_groups(DATA_DIR, load_custom_presets(DATA_DIR))
    manifest_path = Path(__file__).resolve().parent.parent / "docs" / "outlook-addin-manifest.xml"

    template = """
<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Outlook settings</title>
    <style>
      :root { --bg:#0b1220; --panel:#0f172a; --border:#1f2937; --text:#e5e7eb; --muted:#9ca3af; --accent:#22d3ee; }
      * { box-sizing:border-box; }
      body { margin:0; background:var(--bg); color:var(--text); font-family:'Segoe UI', system-ui, -apple-system, sans-serif; padding:16px; display:grid; gap:12px; }
      .card { background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:12px; box-shadow:0 10px 20px rgba(0,0,0,0.28); display:grid; gap:10px; }
      label { font-weight:700; font-size:13px; }
      input, button { border-radius:10px; border:1px solid var(--border); background:rgba(255,255,255,0.03); color:var(--text); padding:10px; font-size:14px; width:100%; }
      button { cursor:pointer; font-weight:800; background:linear-gradient(135deg,var(--accent),#3b82f6); color:#0b0f1a; border:none; }
      .grid { display:grid; gap:8px; }
      .muted { color:var(--muted); font-size:13px; }
      .pill { display:inline-flex; gap:6px; align-items:center; padding:6px 10px; border-radius:999px; border:1px solid var(--border); background:rgba(255,255,255,0.06); font-size:13px; }
      a { color:#7dd3fc; }
    </style>
  </head>
  <body>
    <div class=\"card\">
      <h2 style=\"margin:0;\">API settings</h2>
      <div class=\"muted\">Open from the Outlook add-in to update your base URL, model, and API key in a pop-out window.</div>
      <form method=\"post\" action=\"{{ url_for('save_api_settings') }}\" class=\"grid\">
        <input name=\"base_custom\" placeholder=\"API Base\" value=\"{{ settings.base_url or '' }}\" />
        <input name=\"model\" placeholder=\"Model name\" value=\"{{ settings.model or '' }}\" />
        <input name=\"api_key\" type=\"password\" placeholder=\"API Key\" value=\"{{ settings.api_key or '' }}\" />
        <input name=\"temperature\" type=\"number\" step=\"0.05\" min=\"0\" max=\"2\" placeholder=\"Temperature\" value=\"{{ settings.temperature if settings.temperature is not none else '' }}\" />
        <input name=\"top_p\" type=\"number\" step=\"0.05\" min=\"0\" max=\"1\" placeholder=\"Top-p\" value=\"{{ settings.top_p if settings.top_p is not none else '' }}\" />
        <input name=\"top_k\" type=\"number\" step=\"1\" min=\"1\" placeholder=\"Top-k\" value=\"{{ settings.top_k if settings.top_k is not none else '' }}\" />
        <input type=\"hidden\" name=\"redirect_to\" value=\"outlook_settings\" />
        <button type=\"submit\">Save and return</button>
      </form>
    </div>
    <div class=\"card\">
      <h3 style=\"margin:0;\">Resources snapshot</h3>
      <div class=\"muted\">Preset groups and manuals stay editable in the main workspace. This pop-out keeps them visible for Outlook usage.</div>
      <div class=\"pill\">Preset groups: {{ preset_groups|length }}</div>
      <div class=\"pill\">Manual entries: {{ manual_entries|length }}</div>
      <div class=\"muted\">Full editor: <a href=\"{{ url_for('index') }}\" target=\"_blank\">open workspace</a></div>
    </div>
    <div class=\"card\">
      <h3 style=\"margin:0;\">Sideload manifest</h3>
      <div class=\"muted\">Use the XML manifest below to sideload the add-in. Update the source URL to match your server if needed.</div>
      <pre style=\"background:rgba(255,255,255,0.04); border:1px solid var(--border); border-radius:10px; padding:10px; white-space:pre-wrap;\">{{ manifest_path }}</pre>
    </div>
  </body>
</html>
"""

    return render_template_string(
        template,
        settings=settings,
        manual_entries=manual_entries,
        preset_groups=preset_groups,
        manifest_path=str(manifest_path),
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
        preset_group_ids=[],
        manual_kinds=[],
    )
    sessions.insert(0, session)
    _save_chats(DATA_DIR, sessions)
    return redirect(url_for("index", chat_id=session.id))


@app.route("/chat/delete", methods=["POST"])
def delete_chat():
    chat_id = request.form.get("chat_id")
    sessions = _load_chats(DATA_DIR)
    remaining = [s for s in sessions if s.id != chat_id]
    if len(remaining) == len(sessions):
        flash("未找到要删除的对话。")
        return redirect(url_for("index"))

    if not remaining:
        remaining.append(_default_chat())

    _save_chats(DATA_DIR, remaining)
    append_history(DATA_DIR, new_entry("chat_deleted", {"chat_id": chat_id}))
    return redirect(url_for("index", chat_id=remaining[0].id))


@app.route("/chat/send", methods=["POST"])
def send_message():
    user_message = (request.form.get("message") or "").strip()
    if not user_message:
        flash("请输入内容后再发送。")
        return redirect(url_for("index"))

    chat_id = request.form.get("chat_id")
    preset_group_ids = request.form.getlist("preset_group_ids")
    manual_groups = request.form.getlist("manual_groups")
    settings = _settings_from_form(request.form)

    sessions, active = _ensure_sessions(DATA_DIR, chat_id)
    active.manual_kinds = manual_groups
    active.preset_group_ids = preset_group_ids
    active.messages.append(ChatMessage(role="user", content=user_message))

    preset_groups = _sync_preset_groups(DATA_DIR, load_custom_presets(DATA_DIR))
    preset_map = load_custom_presets(DATA_DIR)
    selected_ids = set(preset_group_ids)
    chosen_entries = [entry for entry in preset_map.values() if entry.group_id in selected_ids]

    # 兼容旧的组提示字段：若该组有 prompt 且没有条目，也作为条目加入
    for group in preset_groups:
        if group.get("prompt") and group.get("id") in selected_ids:
            chosen_entries.append(
                PresetEntry(
                    id=group.get("id", uuid.uuid4().hex[:8]),
                    name=f"{group.get('name', '预设组')}提示",
                    content=str(group.get("prompt", "")),
                    group_id=group.get("id", ""),
                )
            )

    try:
        reply = _build_chat_reply(active, user_message, chosen_entries, settings, manual_groups)
    except Exception as exc:  # noqa: BLE001
        _save_chats(DATA_DIR, sessions)
        append_history(
            DATA_DIR,
            new_entry(
                "chat_error",
                {
                    "chat_id": active.id,
                    "message": user_message,
                    "preset_groups": preset_group_ids,
                    "error": str(exc),
                },
            ),
        )
        flash(f"回复生成失败：{exc}")
        return _render(
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
                "preset_groups": preset_group_ids,
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
    return _render(reply=reply, chat_id=active.id)


@app.route("/preset/delete", methods=["POST"])
def delete_preset():
    entry_id = (request.form.get("entry_id") or "").strip()
    if not entry_id:
        flash("请选择要删除的预设条目。")
        return redirect(url_for("index"))

    if not delete_preset_entry(DATA_DIR, entry_id):
        flash("未找到对应的预设条目。")
        return redirect(url_for("index"))

    _sync_preset_groups(DATA_DIR, load_custom_presets(DATA_DIR))
    message = "已删除预设条目"
    append_history(DATA_DIR, new_entry("preset_deleted", {"id": entry_id}))
    return _render(message=message)


@app.route("/preset/group", methods=["POST"])
def manage_preset_group():
    action = request.form.get("action") or ""
    groups = _load_preset_groups(DATA_DIR)
    if not any(g.get("id") == "ungrouped" for g in groups):
        groups.insert(0, {"id": "ungrouped", "name": "未分组预设"})

    presets = load_custom_presets(DATA_DIR)

    if action == "add":
        name = (request.form.get("group_name") or "").strip()
        prompt = (request.form.get("group_prompt") or "").strip()
        if not name:
            flash("请输入组名称。")
            return redirect(url_for("index"))
        if any(g.get("name") == name for g in groups):
            flash("已存在同名预设组。")
            return redirect(url_for("index"))
        groups.append({"id": uuid.uuid4().hex[:8], "name": name, "prompt": prompt})
        message = f"已新增预设组：{name}"
    elif action in {"rename", "update"}:
        group_id = request.form.get("group_id") or ""
        new_name = (request.form.get("group_name") or "").strip()
        prompt = request.form.get("group_prompt")
        target = next((g for g in groups if g.get("id") == group_id), None)
        if not target:
            flash("未找到要修改的预设组。")
            return redirect(url_for("index"))
        if new_name:
            target["name"] = new_name
        if prompt is not None:
            target["prompt"] = prompt.strip()
        message = f"已更新预设组：{target['name']}"
    elif action == "delete":
        group_id = request.form.get("group_id") or ""
        if group_id == "ungrouped":
            flash("未分组不可删除。")
            return redirect(url_for("index"))
        target = next((g for g in groups if g.get("id") == group_id), None)
        if not target:
            flash("未找到要删除的预设组。")
            return redirect(url_for("index"))

        updated_presets = {}
        changed = False
        for entry in presets.values():
            if entry.group_id == group_id:
                changed = True
                updated_presets[entry.id] = replace(entry, group_id="ungrouped")
            else:
                updated_presets[entry.id] = entry
        if changed:
            save_custom_presets(DATA_DIR, updated_presets)

        groups = [g for g in groups if g.get("id") != group_id]
        message = "已删除预设组，条目已移动到未分组"
    else:
        flash("未知操作。")
        return redirect(url_for("index"))

    _save_preset_groups(DATA_DIR, groups)
    _sync_preset_groups(DATA_DIR, load_custom_presets(DATA_DIR))
    return _render(message=message)


@app.route("/preset", methods=["POST"])
def save_preset():
    entry_id = (request.form.get("entry_id") or "").strip() or uuid.uuid4().hex[:8]
    name = (request.form.get("preset_name") or "").strip()
    content = (request.form.get("preset_content") or "").strip()
    group_id = (request.form.get("preset_group_id") or "").strip()
    group_new = (request.form.get("preset_group_new") or "").strip()

    if not name or not content:
        flash("请填写名称和内容。")
        return redirect(url_for("index"))

    groups = _load_preset_groups(DATA_DIR)
    if not any(g.get("id") == "ungrouped" for g in groups):
        groups.insert(0, {"id": "ungrouped", "name": "未分组预设"})

    if group_new:
        group_id = uuid.uuid4().hex[:8]
        groups.append({"id": group_id, "name": group_new})
    group_id = group_id or "ungrouped"

    upsert_preset_entry(
        DATA_DIR,
        PresetEntry(
            id=entry_id,
            name=name,
            content=content,
            group_id=group_id,
        ),
    )
    _save_preset_groups(DATA_DIR, groups)
    _sync_preset_groups(DATA_DIR, load_custom_presets(DATA_DIR))
    message = f"已保存预设条目：{name}"
    append_history(
        DATA_DIR,
        new_entry(
            "preset_saved",
            {"id": entry_id, "name": name, "group_id": group_id},
        ),
    )
    return _render(message=message, chat_id=request.form.get("chat_id"))


@app.route("/manual/upload", methods=["POST"])
def upload_manual():
    file = request.files.get("manual_pdf")
    title = (request.form.get("manual_title") or "").strip()
    kind = (request.form.get("manual_kind") or "").strip() or "manual"

    if not file or not file.filename:
        flash("请选择要上传的 PDF 文件。")
        return redirect(url_for("index"))

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".pdf"):
        flash("仅支持上传 PDF 文件。")
        return redirect(url_for("index"))

    uploads_dir = DATA_DIR / "manual_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    save_path = uploads_dir / filename
    file.save(save_path)
    entry = append_manual_from_pdf(save_path, DATA_DIR, kind=kind, title=title or None)
    message = f"已入库：{entry['title']}（组：{entry['kind']}）"
    append_history(
        DATA_DIR,
        new_entry(
            "manual_upload",
            {
                "filename": filename,
                "title": entry["title"],
                "source": str(save_path),
                "kind": entry["kind"],
            },
        ),
    )

    names = _load_manual_group_names(DATA_DIR)
    if kind not in names:
        names.append(kind)
        _save_manual_group_names(DATA_DIR, names)

    return _render(message=message)


@app.route("/manual/upload/batch", methods=["POST"])
def upload_manual_batch():
    files = request.files.getlist("manual_pdfs")
    files = [f for f in files if f and f.filename]
    kind = (request.form.get("manual_kind") or "").strip() or "manual"
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
        entry = append_manual_from_pdf(save_path, DATA_DIR, kind=kind)
        messages.append(f"已入库：{entry['title']}（{filename}，组：{kind}）")
        append_history(
            DATA_DIR,
            new_entry(
                "manual_upload",
                {
                    "filename": filename,
                    "title": entry["title"],
                    "source": str(save_path),
                    "kind": entry["kind"],
                },
            ),
        )

    if not messages:
        flash("未处理任何文件，请确认格式。")
        return redirect(url_for("index"))

    names = _load_manual_group_names(DATA_DIR)
    if kind not in names:
        names.append(kind)
        _save_manual_group_names(DATA_DIR, names)

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
    redirect_to = request.form.get("redirect_to")
    if redirect_to in {"outlook_settings", "outlook_home"}:
        return redirect(url_for(redirect_to))
    return _render(message="已保存 API 配置信息")


def _ssl_context():
    """Return an SSL context if HTTPS is requested via environment variables."""

    ssl_cert = os.environ.get("LLM_MAILER_SSL_CERT")
    ssl_key = os.environ.get("LLM_MAILER_SSL_KEY")
    if ssl_cert and ssl_key:
        return ssl_cert, ssl_key

    # Fallback to Werkzeug's adhoc certificate for quick local testing.
    ssl_mode = (os.environ.get("LLM_MAILER_SSL") or "").lower()
    if ssl_mode in {"1", "true", "on", "adhoc"}:
        return "adhoc"

    return None


def main() -> None:
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 7999)),
        debug=False,
        ssl_context=_ssl_context(),
    )


if __name__ == "__main__":
    main()
