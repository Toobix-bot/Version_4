from __future__ import annotations
"""Lightweight repository indexer.

Scans source tree (default: top-level *.py, *.md, *.txt) building an in-memory
index of filename -> text, plus a tiny inverted token map for fuzzy contain search.
Designed to stay fast (< ~1s on small repos) and bounded.
"""
from pathlib import Path
from typing import Dict, List, Iterable, Tuple, Set
import re, time, os, math
from . import metrics as _metrics

DEFAULT_PATTERNS = ("*.py","*.md","*.txt")
MAX_FILE_BYTES = 120_000  # per file safeguard
MAX_TOTAL_BYTES = 3_000_000
TOKEN_RE = re.compile(r"[A-Za-z0-9_]{3,40}")
CAMEL_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")

class CodeIndex:
    # attribute type hints (class-level for simpler parsing)
    files: Dict[str,str]
    tokens: Dict[str,List[str]]
    built_ts: float | None
    total_bytes: int
    embeddings: Dict[str,Dict[str,float]]
    blacklist: List[str]

    def __init__(self) -> None:
        # initialize in-memory structures
        self.files = {}
        self.tokens = {}
        self.built_ts = None
        self.total_bytes = 0
        self.embeddings = {}
        self.blacklist = []

    def build(self, root: Path, patterns: Iterable[str] = DEFAULT_PATTERNS) -> None:
        start = time.time()
        files: Dict[str,str] = {}
        total = 0
        bl_raw = os.getenv('INDEX_EXCLUDE','').strip()
        blacklist: List[str] = [b.strip() for b in bl_raw.split(',') if b.strip()]
        for pat in patterns:
            for p in root.rglob(pat):
                if not p.is_file():
                    continue
                try:
                    data = p.read_text(encoding='utf-8', errors='replace')
                except Exception:
                    continue
                if _looks_binary_or_minified(data):
                    continue
                if not data.strip():
                    continue
                b = len(data.encode('utf-8'))
                if b > MAX_FILE_BYTES:
                    data = data[:MAX_FILE_BYTES]
                    b = len(data.encode('utf-8'))
                if total + b > MAX_TOTAL_BYTES:
                    break
                rel = p.relative_to(root).as_posix()
                if any(rel.endswith(x) or rel.startswith(x) for x in blacklist):
                    continue
                files[rel] = data
                total += b
        tokens: Dict[str,List[str]] = {}
        embeddings: Dict[str,Dict[str,float]] = {}
        for fname, text in files.items():
            seen: set[str] = set()
            freq: Dict[str,int] = {}
            for m in TOKEN_RE.finditer(text[:40_000]):
                raw_tok = m.group(0)
                subs = _split_token_variants(raw_tok)
                for tok in subs:
                    if tok in seen:
                        continue
                    seen.add(tok)
                    tokens.setdefault(tok, []).append(fname)
                    freq[tok] = freq.get(tok,0)+1
            # simple l2-normalized embedding weights
            if os.getenv('ENABLE_EMBED_INDEX') == '1':
                norm = math.sqrt(sum(v*v for v in freq.values())) or 1.0
                embeddings[fname] = {k: v / norm for k,v in freq.items()}
        self.files = files
        self.tokens = tokens
        self.built_ts = time.time()
        self.total_bytes = total
        self.embeddings = embeddings
        self.blacklist = blacklist
        _metrics.record_index_build(len(files), total)
        # minimal log (optional)
        if os.getenv('INDEX_DEBUG') == '1':
            print(f"[index] built {len(files)} files {total} bytes in {self.built_ts-start:.3f}s")

    def search_tokens(self, query: str, limit: int = 20) -> List[Tuple[str,int]]:
        q = query.strip().lower()
        if not q or len(q) < 3:
            return []
        parts = _expand_query(q)
        scores: Dict[str,int] = {}
        for part in parts[:5]:
            lst = self.tokens.get(part)
            if not lst:
                continue
            for f in lst:
                scores[f] = scores.get(f,0)+1
        ranked = sorted(scores.items(), key=lambda x:(-x[1], x[0]))
        return ranked[:limit]

    def semantic_search(self, query: str, limit: int = 10) -> List[Tuple[str,float]]:
        if os.getenv('ENABLE_EMBED_INDEX') != '1':
            return []
        q_tokens: Dict[str,int] = {}
        for tok in _expand_query(query):
            q_tokens[tok] = q_tokens.get(tok,0)+1
        norm_q = math.sqrt(sum(v*v for v in q_tokens.values())) or 1.0
        q_vec: Dict[str,float] = {k: v / norm_q for k,v in q_tokens.items()}
        scores: List[Tuple[str,float]] = []
        for fname, vec in self.embeddings.items():
            s = 0.0
            for k,v in q_vec.items():
                if k in vec:
                    s += v * vec[k]
            if s>0:
                scores.append((fname,s))
        scores.sort(key=lambda x:(-x[1], x[0]))
        return scores[:limit]

    def update_files(self, root: Path, changed: Iterable[str]) -> None:
        """Incrementally reindex specific relative file paths (if they still match filters)."""
        if not self.files:
            return self.build(root)
        patterns = DEFAULT_PATTERNS
        # remove old entries for those files
        to_remove: Set[str] = set(changed)
        if not to_remove:
            return
        # purge tokens
        for tok, flist in list(self.tokens.items()):
            new_list = [f for f in flist if f not in to_remove]
            if new_list:
                self.tokens[tok] = new_list
            else:
                self.tokens.pop(tok, None)
        for f in to_remove:
            self.files.pop(f, None)
            self.embeddings.pop(f, None)
        added_files = 0
        added_bytes = 0
        for rel in to_remove:
            p = root / rel
            if not p.exists() or not p.is_file():
                continue
            if not any(p.match(pat.replace('**/','')) for pat in patterns):
                continue
            try:
                data = p.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            if _looks_binary_or_minified(data):
                continue
            if not data.strip():
                continue
            b = len(data.encode('utf-8'))
            if b > MAX_FILE_BYTES:
                data = data[:MAX_FILE_BYTES]
                b = len(data.encode('utf-8'))
            self.files[rel] = data
            added_bytes += b
            added_files += 1
            seen: set[str] = set()
            freq: Dict[str,int] = {}
            for m in TOKEN_RE.finditer(data[:40_000]):
                raw_tok = m.group(0)
                for tok in _split_token_variants(raw_tok):
                    if tok in seen:
                        continue
                    seen.add(tok)
                    self.tokens.setdefault(tok, []).append(rel)
                    freq[tok] = freq.get(tok,0)+1
            if os.getenv('ENABLE_EMBED_INDEX') == '1':
                norm = math.sqrt(sum(v*v for v in freq.values())) or 1.0
                self.embeddings[rel] = {k: v / norm for k,v in freq.items()}
        if added_files:
            self.total_bytes = sum(len(v.encode('utf-8')) for v in self.files.values())
            _metrics.record_index_build(len(self.files), self.total_bytes)


def _split_token_variants(tok: str) -> List[str]:
    t = tok.lower()
    parts = [t]
    # camel / snake splitting
    if any(c.isupper() for c in tok) or ('_' in tok):
        for seg in CAMEL_RE.findall(tok):
            segl = seg.lower()
            if 3 <= len(segl) <= 40:
                parts.append(segl)
    if '_' in tok:
        for seg in tok.split('_'):
            segl = seg.lower()
            if 3 <= len(segl) <= 40:
                parts.append(segl)
    return list(dict.fromkeys(p for p in parts if len(p)>=3))

def _expand_query(q: str) -> List[str]:
    base = [p for p in re.split(r"\W+", q) if p]
    expanded: List[str] = []
    for b in base:
        expanded.extend(_split_token_variants(b))
    # de-dup preserve order
    out: List[str] = []
    seen: Set[str] = set()
    for t in expanded:
        if t not in seen:
            seen.add(t); out.append(t)
    return out

_global_index: CodeIndex | None = None

def ensure_index(root: Path) -> CodeIndex:
    global _global_index
    import os as _os, time as _time
    ttl = int(_os.getenv('INDEX_REBUILD_TTL_SEC','0') or '0')  # 0 = aus
    apply_thresh = int(_os.getenv('INDEX_REBUILD_APPLIES','0') or '0')  # 0 = aus
    if _global_index is None:
        _global_index = CodeIndex(); _global_index.build(root)
        return _global_index
    # optionale Rebuild Conditions
    should = False
    if ttl > 0 and _global_index.built_ts and (_time.time() - _global_index.built_ts) > ttl:
        should = True
    if apply_thresh > 0:
        try:
            from . import metrics as _m
            # wenn total_files_touched seit Build größer Schwellwert -> rebuild
            # (Heuristik: differenz approximieren; hier einfach absolute Zahl)
            if _m.export_metrics().get('total_files_touched',0) >= apply_thresh:
                should = True
        except Exception:
            pass
    if should:
        try:
            _global_index.build(root)
            try:
                from .metrics import inc_index_auto_rebuild as _inc_auto
                _inc_auto()
            except Exception:
                pass
        except Exception:
            pass
    return _global_index

# --- Binary / Minified Heuristics Utilities --- #
def _looks_binary_or_minified(data: str) -> bool:
    if not data:
        return False
    sample = data[:4000]
    # high ratio of non-printable (excluding common whitespace)
    non_print = sum(1 for c in sample if ord(c) < 9 or (13 < ord(c) < 32))
    if len(sample) and (non_print / len(sample)) > 0.15:
        return True
    # very long average line length (minified) heuristic
    lines = sample.splitlines()
    if lines:
        avg = sum(len(l) for l in lines) / max(1, len(lines))
        if avg > 280 and len(lines) < 30:  # dense chunk
            return True
    return False