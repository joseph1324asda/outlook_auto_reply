from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

from flask import Flask, redirect, render_template_string, request, url_for

from .ai_client import get_provider
from .config import Settings, load_saved_settings, save_settings
from .history import append_history, mask_api_key, new_entry
from .knowledge_base import KnowledgeBase
from .prompt_builder import PromptBuilder, PromptContext
from .styles import PresetEntry, StylePreset, combine_entries, load_custom_presets


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


def _manual_files(data_dir: Path) -> List[Tuple[Path, str]]:
    return [
        (data_dir / "manuals.json", "manuals"),
        (data_dir / "cases.json", "cases"),
    ]


def _load_manual_entries(data_dir: Path) -> List[dict]:
    entries: List[dict] = []
    for path, dataset in _manual_files(data_dir):
        if not path.exists():
            continue
        try:
            items = json.loads(path.read_text(encoding="utf-8")) or []
        except json.JSONDecodeError:
            items = []
        for item in items:
            entries.append({"dataset": dataset, **item})
    return entries


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
    for group in groups:
        content = grouped_entry_content.get(group.get("id", fallback_id)) or []
        normalized.append(
            {
                "id": group.get("id", fallback_id),
                "name": group.get("name", fallback_name),
                "prompt": group.get("prompt", ""),
                "content": content,
            }
        )

    missing_groups = set(grouped_entry_content.keys()) - {group.get("id", fallback_id) for group in groups}
    for group_id in missing_groups:
        normalized.append({"id": group_id, "name": fallback_name, "prompt": "", "content": grouped_entry_content[group_id]})

    _save_preset_groups(data_dir, normalized)
    return normalized


def _style_from_group_ids(group_ids: List[str], custom_presets: Dict[str, PresetEntry]) -> StylePreset:
    chosen: List[PresetEntry] = [p for p in custom_presets.values() if p.group_id in set(group_ids)]
    return combine_entries(chosen)


def _build_outlook_reply(
    subject: str,
    body: str,
    user_request: str,
    preset_group_ids: List[str],
    settings: Settings,
) -> str:
    custom_presets = load_custom_presets(DATA_DIR)
    style = _style_from_group_ids(preset_group_ids, custom_presets)

    kb = KnowledgeBase.from_json_files([DATA_DIR / "manuals.json", DATA_DIR / "cases.json"])
    outlook_context = f"Subject: {subject or '(no subject)'}\n\nBody:\n{body or '(no body provided)'}"
    if user_request.strip():
        outlook_context = f"{outlook_context}\n\nUser request: {user_request.strip()}"
    references = kb.search(outlook_context, limit=5)
    prompt = PromptBuilder(style).build(
        PromptContext(email_summary=outlook_context, user_request=user_request.strip() or None),
        references,
    )

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


def _render_outlook(
    reply: str | None = None,
    error: str | None = None,
    subject: str = "",
    body: str = "",
    request_text: str = "",
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
          <div class=\"muted\">Select preset groups, let the add-in read the email, and add any custom request before generating.</div>
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
        <div class=\"grid\">
          <label for=\"request\">Additional request</label>
          <textarea id=\"request\" name=\"request\" placeholder=\"Example: reply in Spanish, highlight key risks, or ask for missing info...\">{{ request_text }}</textarea>
        </div>
        <div class=\"inline\">
          <div class=\"muted\">Outlook-facing form keeps preset selection, message reading, and extra instructions.</div>
          <button type=\"submit\" style=\"max-width:220px;\">Generate reply</button>
        </div>
      </form>
      {% if error %}<div class=\"error\">{{ error }}</div>{% endif %}
      <div class=\"card\">
        <div class=\"muted\">Generated reply</div>
        <div class=\"reply-box\">{{ reply or 'Pending generation...' }}</div>
      </div>
    </div>
    <script>
      const subjectField = document.getElementById('subject');
      const bodyField = document.getElementById('body');
      if (window.Office && Office.onReady) {
        Office.onReady(function(info) {
          try {
            const item = Office.context && Office.context.mailbox && Office.context.mailbox.item;
            if (!item) { return; }
            if (item.subject && !subjectField.value) {
              subjectField.value = item.subject;
            }
            if (item.body && item.body.getAsync) {
              item.body.getAsync('text', { asyncContext: null }, function(res) {
                if (res.status === Office.AsyncResultStatus.Succeeded && !bodyField.value.trim()) {
                  bodyField.value = res.value || '';
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
        request_text=request_text,
        selected_groups=selected_groups,
        settings=settings,
    )


@app.route("/", methods=["GET"])
def outlook_root_redirect():
    return redirect(url_for("outlook_home"))


@app.route("/outlook", methods=["GET"])
def outlook_home():
    return _render_outlook()


@app.route("/outlook/generate", methods=["POST"])
def outlook_generate():
    subject = (request.form.get("subject") or "").strip()
    body = (request.form.get("body") or "").strip()
    request_text = (request.form.get("request") or "").strip()
    preset_group_ids = request.form.getlist("preset_groups")
    settings = _settings_from_form(request.form)

    try:
        reply = _build_outlook_reply(subject, body, request_text, preset_group_ids, settings)
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
            error=f"生成回复失败：{exc}",
            subject=subject,
            body=body,
            request_text=request_text,
            selected_groups=preset_group_ids,
        )

    append_history(
        DATA_DIR,
        new_entry(
            "outlook_reply",
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
        request_text=request_text,
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
      <div class=\"muted\">Update your base URL, model, and API key without leaving Outlook.</div>
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
      <div class=\"muted\">Preset groups and manuals are kept for Outlook replies. Manage the JSON files on the server to update them.</div>
      <div class=\"pill\">Preset groups: {{ preset_groups|length }}</div>
      <div class=\"pill\">Manual entries: {{ manual_entries|length }}</div>
      <div class=\"muted\">Manifest path: <code>{{ manifest_path }}</code></div>
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
    return redirect(url_for("outlook_home"))


def main() -> None:
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)


if __name__ == "__main__":
    main()
