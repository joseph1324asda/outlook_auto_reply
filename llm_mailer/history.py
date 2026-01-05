from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class HistoryEntry:
    action: str
    detail: Dict[str, Any]
    timestamp: str


def mask_api_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 6:
        return "***"
    return f"{api_key[:3]}...{api_key[-2:]}"


def load_history(data_dir: Path) -> List[HistoryEntry]:
    history_file = data_dir / "history.json"
    if not history_file.exists():
        return []
    data = json.loads(history_file.read_text(encoding="utf-8")) or []
    entries: List[HistoryEntry] = []
    for raw in data:
        entries.append(
            HistoryEntry(
                action=str(raw.get("action", "")),
                detail=dict(raw.get("detail", {})),
                timestamp=str(raw.get("timestamp", "")),
            )
        )
    return entries


def append_history(data_dir: Path, entry: HistoryEntry, limit: int = 50) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    history = load_history(data_dir)
    history.insert(0, entry)
    history = history[:limit]
    history_file = data_dir / "history.json"
    with history_file.open("w", encoding="utf-8") as f:
        json.dump([asdict(item) for item in history], f, ensure_ascii=False, indent=2)


def new_entry(action: str, detail: Dict[str, Any]) -> HistoryEntry:
    return HistoryEntry(
        action=action,
        detail=detail,
        timestamp=datetime.utcnow().isoformat() + "Z",
    )
