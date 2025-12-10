from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import requests

from .config import Settings


class AiProvider(Protocol):
    def generate(self, prompt: str) -> str:
        ...


def get_provider(settings: Settings) -> AiProvider:
    return HttpChatProvider(settings=settings)


@dataclass
class HttpChatProvider:
    """Simple chat-completion provider using an OpenAI-compatible endpoint."""

    settings: Settings

    def generate(self, prompt: str) -> str:
        if not self.settings.api_key:
            raise RuntimeError("未配置 API Key，无法调用模型")

        base_url = (self.settings.base_url or "https://api.openai.com/v1").rstrip("/")
        model = self.settings.model or "gpt-4o-mini"
        url = f"{base_url}/chat/completions"

        headers = {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"请求模型接口失败：{exc}") from exc

        if not resp.ok:
            raise RuntimeError(f"模型接口返回 {resp.status_code}：{resp.text}")

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"无法解析模型返回：{resp.text}") from exc

        try:
            return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("模型返回格式缺少内容字段") from exc
