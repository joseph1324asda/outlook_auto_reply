from __future__ import annotations

import argparse
from pathlib import Path
from typing import List

from .ai_client import get_provider
from .config import Settings
from .knowledge_base import KnowledgeBase
from .prompt_builder import PromptBuilder, PromptContext
from .styles import combine_presets, merged_presets


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM 邮件助手")
    parser.add_argument("email", help="邮件摘要或正文")
    parser.add_argument(
        "--style",
        dest="styles",
        action="append",
        help="可多次指定以组合多个风格预设，默认使用 concise_business",
    )
    parser.add_argument("--priority", default=None, help="客户优先级，例如 P0/P1")
    parser.add_argument("--attachments", nargs="*", default=[], help="附件名列表")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="包含 manuals.json 与 cases.json 的目录",
    )
    parser.add_argument("--model", default=None, help="自定义模型名称（可选）")
    parser.add_argument("--api-key", default=None, help="LLM API Key（可选）")
    parser.add_argument("--base-url", default=None, help="LLM API Base URL（可选）")
    return parser


def load_knowledge_base(data_dir: Path) -> KnowledgeBase:
    manuals = data_dir / "manuals.json"
    cases = data_dir / "cases.json"
    return KnowledgeBase.from_json_files([manuals, cases])


def draft_email_reply(
    email: str,
    style_keys: List[str],
    priority: str | None,
    attachments: List[str],
    data_dir: Path,
    settings: Settings | None = None,
) -> str:
    settings = settings or Settings.from_env()
    kb = load_knowledge_base(data_dir)
    style_presets = merged_presets(data_dir)

    missing = [key for key in style_keys if key not in style_presets]
    if missing:
        raise SystemExit(f"未知的风格预设: {', '.join(missing)}")

    style = combine_presets(style_presets, style_keys)

    context = PromptContext(email_summary=email, customer_priority=priority, attachments=attachments)
    references = kb.search(email)
    prompt = PromptBuilder(style).build(context, references)

    provider = get_provider(settings)
    return provider.generate(prompt)


def main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()
    styles = args.styles or ["concise_business"]

    env_settings = Settings.from_env()
    settings = Settings(
        api_key=args.api_key or env_settings.api_key,
        model=args.model or env_settings.model,
        base_url=args.base_url or env_settings.base_url,
        temperature=env_settings.temperature,
        top_p=env_settings.top_p,
        top_k=env_settings.top_k,
    )

    reply = draft_email_reply(
        email=args.email,
        style_keys=styles,
        priority=args.priority,
        attachments=args.attachments,
        data_dir=args.data_dir,
        settings=settings,
    )
    print(reply)


if __name__ == "__main__":
    main()
