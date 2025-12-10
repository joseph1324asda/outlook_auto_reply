from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class StylePreset:
    name: str
    tone: str
    structure: List[str]
    closing: str
    reminders: List[str]


def default_style_presets() -> Dict[str, StylePreset]:
    return {
        "concise_business": StylePreset(
            name="简洁商务",
            tone="语气专业、清晰、直入主题。",
            structure=[
                "致谢或礼貌问候",
                "核心回复要点分段罗列",
                "下一步行动与时间预期",
            ],
            closing="感谢您的配合，期待您的确认。",
            reminders=[
                "避免长句，突出可执行信息",
                "数字或日期优先使用阿拉伯数字",
            ],
        ),
        "engineering_detail": StylePreset(
            name="工程细节",
            tone="保持工程师语气，强调假设、限制与验证步骤。",
            structure=[
                "问题理解与假设",
                "方案或排查步骤（列表）",
                "风险与注意事项",
                "验证计划或需求",
            ],
            closing="如需进一步数据或日志，请告知。",
            reminders=[
                "明确输入输出与边界条件",
                "给出可被验证的步骤或公式",
            ],
        ),
        "friendly_success": StylePreset(
            name="友好客户成功",
            tone="语气亲和，强调我们会协助解决。",
            structure=[
                "同理与感谢",
                "问题概述",
                "解决方案或下一步",
                "资源链接或联系人",
            ],
            closing="我们随时待命，祝您工作顺利！",
            reminders=[
                "保持句式简短易读",
                "使用积极表述，减少否定句",
            ],
        ),
    }


def _deserialize_preset(raw: dict) -> StylePreset:
    return StylePreset(
        name=raw["name"],
        tone=raw["tone"],
        structure=list(raw.get("structure", [])),
        closing=raw["closing"],
        reminders=list(raw.get("reminders", [])),
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
        "tone": preset.tone,
        "structure": list(preset.structure),
        "closing": preset.closing,
        "reminders": list(preset.reminders),
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

    tone = " / ".join(p.tone for p in selected)
    structure: List[str] = []
    reminders: List[str] = []
    for preset in selected:
        structure.extend(preset.structure)
        reminders.extend(preset.reminders)

    # 去重但保持顺序
    def _dedup(items: List[str]) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    structure = _dedup(structure)
    reminders = _dedup(reminders)

    closing = " / ".join(_dedup([p.closing for p in selected]))
    name = " + ".join(p.name for p in selected)
    return StylePreset(
        name=name,
        tone=f"组合风格：{tone}",
        structure=structure,
        closing=closing,
        reminders=reminders,
    )
