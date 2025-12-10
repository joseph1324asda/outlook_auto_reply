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


class PromptBuilder:
    def __init__(self, style: StylePreset):
        self.style = style

    def build(self, context: PromptContext, references: List[Document]) -> str:
        def _fmt_ref(doc: Document) -> str:
            source = f"（来源：{doc.source}）" if doc.source else ""
            return f"- [{doc.kind}] {doc.title}{source}: {doc.content}"

        ref_lines = [_fmt_ref(doc) for doc in references]
        refs = "\n".join(ref_lines) if ref_lines else "- （未命中，需基于通用知识回答）"

        structure_lines = "\n".join(f"- {item}" for item in self.style.structure)
        reminder_lines = "\n".join(f"- {item}" for item in self.style.reminders)
        attachments = ", ".join(context.attachments or []) or "无"

        return f"""
你是一名邮件助理，负责产出高质量的中文邮件。请遵循以下要求：
- 语气：{self.style.tone}
- 结构：\n{structure_lines}
- 结尾：{self.style.closing}
- 提醒：\n{reminder_lines}

用户邮件摘要：{context.email_summary}
客户优先级：{context.customer_priority or '未标注'}
附件列表：{attachments}

知识库参考：
{refs}

请生成一封完整的邮件回复，使用分段与列表增强可读性。
""".strip()
