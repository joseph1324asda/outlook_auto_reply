from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class StylePreset:
    name: str
    content: str


@dataclass(frozen=True)
class PresetEntry:
    id: str
    name: str
    content: str
    group_id: str


def _deserialize_entry(raw_id: str, raw: dict) -> PresetEntry:
    # 兼容旧字段，若不存在 content 则拼合旧的语气/结构/结尾/提醒
    content = raw.get("content")
    if content is None:
        tone = raw.get("tone", "")
        structure = " | ".join(raw.get("structure", []))
        closing = raw.get("closing", "")
        reminders = " / ".join(raw.get("reminders", []))
        parts = [tone, structure, closing, reminders]
        content = "\n".join([p for p in parts if p]) or "保持礼貌、清晰、可执行。"

    return PresetEntry(
        id=raw.get("id") or raw_id or str(uuid.uuid4()),
        name=raw.get("name", raw_id),
        content=content,
        group_id=raw.get("group_id") or raw.get("group") or "ungrouped",
    )


def load_custom_presets(data_dir: Path) -> Dict[str, PresetEntry]:
    presets_file = data_dir / "presets.json"
    if not presets_file.exists():
        return {}
    with presets_file.open("r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return {}

    if isinstance(data, dict):
        entries = [_deserialize_entry(key, raw) for key, raw in data.items()]
    else:
        entries = [_deserialize_entry(str(item.get("id", "")), item) for item in data]

    return {entry.id: entry for entry in entries}


def save_custom_presets(data_dir: Path, presets: Dict[str, PresetEntry]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    presets_file = data_dir / "presets.json"
    payload = [
        {
            "id": entry.id,
            "name": entry.name,
            "content": entry.content,
            "group_id": entry.group_id,
        }
        for entry in presets.values()
    ]
    with presets_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def upsert_preset_entry(data_dir: Path, entry: PresetEntry) -> None:
    presets = load_custom_presets(data_dir)
    presets[entry.id] = entry
    save_custom_presets(data_dir, presets)


def delete_preset_entry(data_dir: Path, entry_id: str) -> bool:
    presets = load_custom_presets(data_dir)
    if entry_id not in presets:
        return False
    presets.pop(entry_id)
    save_custom_presets(data_dir, presets)
    return True


def combine_entries(entries: List[PresetEntry]) -> StylePreset:
    if not entries:
        return StylePreset(name="默认风格", content="保持礼貌、简洁、可执行。")
    name = " + ".join(entry.name for entry in entries)
    content = "\n\n".join(entry.content for entry in entries)
    return StylePreset(name=name, content=content)
