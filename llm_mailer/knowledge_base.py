from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


@dataclass
class Document:
    id: str
    title: str
    content: str
    kind: str
    source: str | None = None


class KnowledgeBase:
    def __init__(self, documents: Iterable[Document]):
        self.documents = list(documents)

    @classmethod
    def from_json_files(cls, paths: Iterable[Path]) -> "KnowledgeBase":
        docs: List[Document] = []
        for path in paths:
            if not path.exists():
                continue
            loaded = json.loads(path.read_text(encoding="utf-8")) or []
            for entry in loaded:
                docs.append(
                    Document(
                        id=str(entry.get("id")),
                        title=str(entry.get("title", "Untitled")),
                        content=str(entry.get("content", "")),
                        kind=str(entry.get("kind", path.stem)),
                        source=(str(entry.get("source")) if entry.get("source") else None),
                    )
                )
        return cls(docs)

    def search(self, query: str, limit: int = 3) -> List[Document]:
        if not query.strip():
            return []
        keywords = {word.lower() for word in query.replace("/", " ").split() if len(word) > 1}
        scored = []
        for doc in self.documents:
            score = sum(doc.content.lower().count(kw) + doc.title.lower().count(kw) for kw in keywords)
            if score:
                scored.append((score, doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc for _, doc in scored[:limit]]
