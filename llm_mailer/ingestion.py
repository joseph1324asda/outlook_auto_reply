from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List

from pypdf import PdfReader


def extract_pdf_text(pdf_path: Path, max_chars: int = 8000) -> str:
    """Extract text content from a PDF file and normalize whitespace."""

    reader = PdfReader(str(pdf_path))
    chunks: List[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            chunks.append(" ".join(text.split()))

    content = "\n".join(chunks)
    if max_chars and len(content) > max_chars:
        return content[:max_chars]
    return content


def append_manual_from_pdf(pdf_path: Path, data_dir: Path) -> dict:
    """Append a PDF as a manual entry into manuals.json under data_dir."""

    data_dir.mkdir(parents=True, exist_ok=True)
    manuals_path = data_dir / "manuals.json"
    existing: List[dict] = []
    if manuals_path.exists():
        try:
            existing = json.loads(manuals_path.read_text(encoding="utf-8")) or []
        except json.JSONDecodeError:
            existing = []

    content = extract_pdf_text(pdf_path)
    entry = {
        "id": f"PDF-{int(time.time())}",
        "title": pdf_path.stem,
        "kind": "manual",
        "content": content,
        "source": str(pdf_path),
    }
    existing.append(entry)
    manuals_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return entry

