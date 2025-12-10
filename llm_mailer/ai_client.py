from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import Settings


class AiProvider(Protocol):
    def generate(self, prompt: str) -> str:
        ...


def get_provider(settings: Settings) -> AiProvider:
    # Placeholder for real provider selection.
    return LocalTemplateProvider(base_url=settings.base_url, model=settings.model)


@dataclass
class LocalTemplateProvider:
    """A deterministic provider for offline testing."""

    header: str = "【示例回复（本地模板，无需真实 API）】"
    base_url: str | None = None
    model: str | None = None

    def generate(self, prompt: str) -> str:
        meta = []
        if self.model:
            meta.append(f"模型：{self.model}")
        if self.base_url:
            meta.append(f"API：{self.base_url}")
        meta_line = "；".join(meta)
        meta_text = f"\n{meta_line}" if meta else ""
        return f"{self.header}{meta_text}\n\n{prompt}\n\n---\n请用以上要点生成最终邮件。"
