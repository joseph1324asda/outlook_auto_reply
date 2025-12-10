from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, flash, redirect, render_template_string, request, url_for
from werkzeug.utils import secure_filename

from .cli import draft_email_reply
from .config import Settings, load_saved_settings, save_settings
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


def _render(reply: str | None = None, message: str | None = None, selected_styles: list[str] | None = None):
    styles = merged_presets(DATA_DIR)
    settings = load_saved_settings(DATA_DIR)
    selected_styles = selected_styles or ["concise_business"]
    template = """
<!doctype html>
<html lang="zh">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>LLM 邮件助手</title>
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; margin: 2rem auto; max-width: 960px; padding: 0 1rem; }
      h1 { margin-bottom: 0.25rem; }
      form { border: 1px solid #e5e5e5; padding: 1rem; border-radius: 8px; margin-bottom: 1.5rem; }
      label { display: block; font-weight: 600; margin-top: 0.5rem; }
      textarea, input, select { width: 100%; padding: 0.5rem; margin-top: 0.25rem; box-sizing: border-box; }
      button { margin-top: 0.75rem; padding: 0.5rem 1rem; background: #2563eb; color: #fff; border: none; border-radius: 6px; cursor: pointer; }
      button.secondary { background: #6b7280; }
      .card { background: #f9fafb; padding: 1rem; border-radius: 8px; border: 1px solid #e5e7eb; }
      .message { color: #2563eb; font-weight: 600; }
      .flash { color: #b91c1c; font-weight: 600; }
      pre { white-space: pre-wrap; word-break: break-word; }
      .two-col { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); grid-gap: 1rem; }
      .small { font-weight: 400; color: #4b5563; }
      .tag { display: inline-block; background: #e5e7eb; color: #111827; padding: 0.1rem 0.45rem; border-radius: 999px; margin-right: 0.25rem; margin-top: 0.25rem; }
    </style>
  </head>
  <body>
    <h1>LLM 邮件助手</h1>
    <p>填写邮件摘要，选择/组合风格，配置 API 链接后，一键生成草稿；上传 PDF 会自动追加到说明书库。</p>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        <div class="flash">{{ messages[0] }}</div>
      {% endif %}
    {% endwith %}
    {% if message %}<div class="message">{{ message }}</div>{% endif %}

    <div class="two-col">
      <form method="post" action="{{ url_for('draft') }}">
        <h2>生成邮件草稿</h2>
        <label for="email">邮件摘要或正文</label>
        <textarea id="email" name="email" rows="4" required placeholder="例如：客户反馈接口限流，希望提升到每分钟120次"></textarea>

        <label for="style_keys">选择/组合风格预设（可多选）</label>
        <select id="style_keys" name="style_keys" multiple size="5">
          {% for key, preset in styles.items() %}
            <option value="{{ key }}" {% if key in selected_styles %}selected{% endif %}>{{ key }}｜{{ preset.name }}</option>
          {% endfor %}
        </select>
        <div class="small">按住 Ctrl/Cmd 可多选，组合后自动合并语气、结构与提醒。</div>

        <label for="priority">客户优先级（可选）</label>
        <input id="priority" name="priority" placeholder="例如：P1" />

        <label for="attachments">附件名称（换行分隔，可选）</label>
        <textarea id="attachments" name="attachments" rows="2" placeholder="报告.pdf\n日志.txt"></textarea>

        <h3>API 配置（可在此直接修改）</h3>
        <label for="base_choice">常用 API Base</label>
        <select id="base_choice" name="base_choice">
          <option value="">（使用自定义或环境变量）</option>
          {% for base in default_bases %}
            <option value="{{ base }}" {% if settings.base_url == base %}selected{% endif %}>{{ base }}</option>
          {% endfor %}
        </select>

        <label for="base_custom">自定义 API Base URL</label>
        <input id="base_custom" name="base_custom" placeholder="例如：https://api.your-llm.com/v1" value="{{ settings.base_url or '' }}" />

        <label for="model">模型名称</label>
        <input id="model" name="model" placeholder="例如：gpt-4o-mini" value="{{ settings.model or '' }}" />

        <label for="api_key">API Key（保存在本地 app_settings.json）</label>
        <input id="api_key" name="api_key" type="password" placeholder="sk-..." value="{{ settings.api_key or '' }}" />

        <button type="submit">一键生成草稿</button>
      </form>

      <form method="post" action="{{ url_for('upload') }}" enctype="multipart/form-data">
        <h2>上传 PDF 到说明书库</h2>
        <label for="manual_pdf">选择 PDF 文件</label>
        <input type="file" id="manual_pdf" name="manual_pdf" accept="application/pdf" required />
        <button type="submit" class="secondary">上传并入库</button>
      </form>
    </div>

    <form method="post" action="{{ url_for('save_preset') }}">
      <h2>创建 / 修改风格预设</h2>
      <div class="two-col">
        <div>
          <label for="preset_key">预设 Key（英文/数字，唯一）</label>
          <input id="preset_key" name="preset_key" required placeholder="例如：friendly_ops" />

          <label for="preset_name">显示名称</label>
          <input id="preset_name" name="preset_name" required placeholder="例如：友好运维" />

          <label for="preset_tone">语气</label>
          <textarea id="preset_tone" name="preset_tone" rows="2" required placeholder="语气描述"></textarea>
        </div>
        <div>
          <label for="preset_structure">结构（每行一条）</label>
          <textarea id="preset_structure" name="preset_structure" rows="3" placeholder="开场感谢\n要点列表\n下一步行动"></textarea>

          <label for="preset_closing">结尾</label>
          <input id="preset_closing" name="preset_closing" required placeholder="例如：感谢您的配合，期待您的确认。" />

          <label for="preset_reminders">提醒（每行一条）</label>
          <textarea id="preset_reminders" name="preset_reminders" rows="3" placeholder="保持句子简短\n给出可执行指令"></textarea>
        </div>
      </div>
      <button type="submit">保存预设（支持覆盖）</button>
    </form>

    <div class="card">
      <h3>当前风格预设列表</h3>
      {% for key, preset in styles.items() %}
        <div>
          <div class="tag">{{ key }}</div>
          <strong>{{ preset.name }}</strong>｜{{ preset.tone }}<br />
          <div class="small">结构：{{ preset.structure|join(' / ') }}</div>
          <div class="small">提醒：{{ preset.reminders|join(' / ') }}</div>
          <div class="small">结尾：{{ preset.closing }}</div>
        </div>
        <hr />
      {% endfor %}
    </div>

    {% if reply %}
      <div class="card">
        <h3>生成结果</h3>
        <pre>{{ reply }}</pre>
      </div>
    {% endif %}
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
    return _render(message=message)


def main() -> None:
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)


if __name__ == "__main__":
    main()

