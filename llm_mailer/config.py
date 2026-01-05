from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import os


@dataclass
class Settings:
    """Runtime configuration for the assistant."""

    api_key: str | None
    model: str
    base_url: str | None

    def to_dict(self) -> dict:
        return {"api_key": self.api_key, "model": self.model, "base_url": self.base_url}

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        return cls(api_key=data.get("api_key"), model=data.get("model", "gpt-4o-mini"), base_url=data.get("base_url"))

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_key=os.getenv("AI_API_KEY"),
            model=os.getenv("AI_MODEL", "gpt-4o-mini"),
            base_url=os.getenv("AI_BASE_URL"),
        )


def load_saved_settings(data_dir: Path) -> Settings:
    settings_file = data_dir / "app_settings.json"
    if settings_file.exists():
        try:
            with settings_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return Settings.from_dict(data)
        except json.JSONDecodeError:
            # Ignore corrupted settings and fall back to environment defaults.
            pass
    return Settings.from_env()


def save_settings(data_dir: Path, settings: Settings) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    settings_file = data_dir / "app_settings.json"
    with settings_file.open("w", encoding="utf-8") as f:
        json.dump(settings.to_dict(), f, ensure_ascii=False, indent=2)
