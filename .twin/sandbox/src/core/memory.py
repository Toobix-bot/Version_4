from __future__ import annotations
from typing import List
from .models import MemoryItem

class MemoryStore:
    def __init__(self) -> None:
        self.short: List[MemoryItem] = []
        self.mid: List[MemoryItem] = []
        self.long: List[MemoryItem] = []  # Placeholder for embedding-backed store

    def add(self, item: MemoryItem) -> None:
        if item.kind == "short":
            self.short.append(item)
        elif item.kind == "mid":
            self.mid.append(item)
        else:
            self.long.append(item)

    def summarize_short(self) -> MemoryItem:
        content = " | ".join(m.content for m in self.short[-8:])
        summary = MemoryItem(kind="mid", content=f"Summary: {content[:400]}")
        self.add(summary)
        return summary
