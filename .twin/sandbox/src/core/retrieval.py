from __future__ import annotations
from pathlib import Path
from typing import List
from .models import RetrievalResult
import math

IGNORED_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.lock', '.exe', '.dll'}


def naive_scan(root: Path, limit: int = 20, query: str | None = None) -> List[RetrievalResult]:
    results: List[RetrievalResult] = []
    q_tokens = set(query.lower().split()) if query else set()
    for path in root.rglob('*'):
        if path.is_dir():
            continue
        if path.suffix.lower() in IGNORED_EXT:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        base_score = 0.5
        if q_tokens:
            hit = sum(1 for t in q_tokens if t in text.lower())
            base_score += (hit / (len(q_tokens) + 1))
        results.append(RetrievalResult(ref=str(path), snippet=text[:400], score=min(1.0, base_score)))
    # sort by score desc
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]
