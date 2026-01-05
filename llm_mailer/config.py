from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import os


@dataclass
class Settings:
    """Runtime configuration for the assistant."""

    api_key: str | None = None
    model: str = "gpt-4o-mini"
    base_url: str | None = None
    temperature: float | None = 0.7
    top_p: float | None = 0.9
    top_k: int | None = 40

    def to_dict(self) -> dict:
        return {
            "api_key": self.api_key,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "top_k": self.top_k,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        return cls(
            api_key=data.get("api_key"),
            model=data.get("model", "gpt-4o-mini"),
            base_url=data.get("base_url"),
            temperature=data.get("temperature", 0.7),
            top_p=data.get("top_p", 0.9),
            top_k=data.get("top_k", 40),
        )

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_key=os.getenv("AI_API_KEY"),
            model=os.getenv("AI_MODEL", "gpt-4o-mini"),
            base_url=os.getenv("AI_BASE_URL"),
            temperature=float(os.getenv("AI_TEMPERATURE", "0.7")),
            top_p=float(os.getenv("AI_TOP_P", "0.9")),
            top_k=int(os.getenv("AI_TOP_K", "40")),
        )


def load_saved_settings(data_dir: Path) -> Settings:
    settings_file = data_dir / "app_settings.json"
    if settings_file.exists():
        with settings_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return Settings.from_dict(data)
    return Settings.from_env()


def save_settings(data_dir: Path, settings: Settings) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    settings_file = data_dir / "app_settings.json"
    with settings_file.open("w", encoding="utf-8") as f:
        json.dump(settings.to_dict(), f, ensure_ascii=False, indent=2)
