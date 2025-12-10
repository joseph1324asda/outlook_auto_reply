from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class StylePreset:
    name: str
    content: str


def default_style_presets() -> Dict[str, StylePreset]:
    return {
        "concise_business": StylePreset(
            name="简洁商务",
            content=(
                "保持专业直截了当，先致谢或问候，其次用短句列出要点，最后给出下一步和时间节点，避免冗长句式。"
            ),
        ),
        "engineering_detail": StylePreset(
            name="工程细节",
            content=(
                "以工程师视角说明假设、限制与验证步骤，逐条列出排查或方案，强调风险与注意事项，结尾邀请补充日志或数据。"
            ),
        ),
        "friendly_success": StylePreset(
            name="友好客户成功",
            content=(
                "语气亲和，先表示理解与感谢，再概述问题，给出可执行的下一步与资源链接，保持句式简短积极。"
            ),
        ),
    }


def _deserialize_preset(raw: dict) -> StylePreset:
    # 兼容旧字段，若不存在 content 则拼合旧的语气/结构/结尾/提醒
    content = raw.get("content")
    if content is None:
        tone = raw.get("tone", "")
        structure = " | ".join(raw.get("structure", []))
        closing = raw.get("closing", "")
        reminders = " / ".join(raw.get("reminders", []))
        parts = [tone, structure, closing, reminders]
        content = "\n".join([p for p in parts if p]) or "保持礼貌、清晰、可执行。"

    return StylePreset(
        name=raw["name"],
        content=content,
    )


def load_custom_presets(data_dir: Path) -> Dict[str, StylePreset]:
    presets_file = data_dir / "presets.json"
    if not presets_file.exists():
        return {}
    with presets_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {key: _deserialize_preset(raw) for key, raw in data.items()}


def merged_presets(data_dir: Path) -> Dict[str, StylePreset]:
    presets = default_style_presets()
    presets.update(load_custom_presets(data_dir))
    return presets


def save_custom_preset(data_dir: Path, key: str, preset: StylePreset) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    presets_file = data_dir / "presets.json"
    existing = {k: vars(v) for k, v in load_custom_presets(data_dir).items()}
    existing[key] = {
        "name": preset.name,
        "content": preset.content,
    }
    with presets_file.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def combine_presets(style_presets: Dict[str, StylePreset], keys: List[str]) -> StylePreset:
    if not keys:
        raise ValueError("至少需要选择一个风格预设")
    selected = []
    for key in keys:
        if key not in style_presets:
            raise KeyError(f"未知的风格预设: {key}")
        selected.append(style_presets[key])

    if len(selected) == 1:
        return selected[0]

    name = " + ".join(p.name for p in selected)
    content = "\n\n".join(p.content for p in selected)
    return StylePreset(name=name, content=content)
