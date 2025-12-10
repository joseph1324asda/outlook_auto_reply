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
            raise RuntimeError("API key is required before calling the model")

        base_url = (self.settings.base_url or "https://api.openai.com/v1").rstrip("/")
        model = self.settings.model or "gpt-4o-mini"
        url = f"{base_url}/chat/completions"

        headers = {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}]}
        if self.settings.temperature is not None:
            payload["temperature"] = self.settings.temperature
        if self.settings.top_p is not None:
            payload["top_p"] = self.settings.top_p
        if self.settings.top_k is not None:
            payload["top_k"] = self.settings.top_k

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Model request failed: {exc}") from exc

        if not resp.ok:
            raise RuntimeError(f"Model API returned {resp.status_code}: {resp.text}")

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Could not decode model response: {resp.text}") from exc

        try:
            return data["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("Model response did not contain message content") from exc
