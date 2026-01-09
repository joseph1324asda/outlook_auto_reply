from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .knowledge_base import Document
from .styles import StylePreset


@dataclass
class PromptContext:
    email_summary: str
    customer_priority: str | None = None
    attachments: List[str] | None = None
    user_request: str | None = None


class PromptBuilder:
    def __init__(self, style: StylePreset):
        self.style = style

    def build(self, context: PromptContext, references: List[Document]) -> str:
        def _fmt_ref(doc: Document) -> str:
            source = f" (source: {doc.source})" if doc.source else ""
            return f"- [{doc.kind}] {doc.title}{source}: {doc.content}"

        ref_lines = [_fmt_ref(doc) for doc in references]
        refs = "\n".join(ref_lines) if ref_lines else "- (no matches; answer from general knowledge)"

        attachments = ", ".join(context.attachments or []) or "none"
        user_request = context.user_request or "none"

        return f"""
You are an email assistant who must produce a clear, fluent reply in English.
- Style guide: {self.style.name}
- Writing hints: {self.style.content}

User email summary: {context.email_summary}
Customer priority: {context.customer_priority or 'not specified'}
Attachments: {attachments}
Additional user request: {user_request}

Knowledge-base matches:
{refs}

Draft a complete email reply in English. Use paragraphs and lists for readability.
""".strip()
