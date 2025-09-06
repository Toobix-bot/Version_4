from __future__ import annotations
"""Lightweight repository analysis utilities.

Generates high-level improvement suggestions by:
 - Collecting a summary of selected source & doc files (truncated)
 - Combining with current user-defined objectives
 - Sending a structured prompt to the existing Groq client

Returned suggestions are plain dict structures (id, title, rationale, diff_hint).
Conversion into PatchProposal objects is handled by API endpoint /inject-proposal.
"""
from pathlib import Path
from typing import List, Dict, Any, Protocol, Tuple, TypedDict, cast
import hashlib

MAX_FILE_BYTES = 6000  # hard cap per file sample
MAX_TOTAL_BYTES = 32000
ALLOW_SUFFIX = {".py", ".md", ".txt"}
IGNORE = {".venv", "__pycache__", ".git", ".twin", "logs"}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:8]


def collect_repo_sample(root: Path) -> List[Dict[str, Any]]:
    samples: List[Dict[str, Any]] = []
    budget = MAX_TOTAL_BYTES
    for p in sorted(root.rglob('*')):
        if p.is_dir():
            if p.name in IGNORE:
                continue
            if any(part in IGNORE for part in p.relative_to(root).parts):
                continue
            continue
        if p.suffix not in ALLOW_SUFFIX:
            continue
        rel = p.relative_to(root).as_posix()
        try:
            raw = p.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        head = raw[:MAX_FILE_BYTES]
        size = len(head.encode('utf-8', errors='ignore'))
        if budget - size < 0:
            break
        budget -= size
        samples.append({"path": rel, "size": len(raw), "hash": _hash(raw), "snippet": head})
        if budget <= 0:
            break
    return samples


ANALYZE_PROMPT_TEMPLATE = """
You are an autonomous code evolution strategist. Given repository file snippets and high-level user objectives, produce up to 5 concrete improvement suggestions.

Rules:
 - Output STRICT JSON array (no prose) of objects: [{"id": "s1", "title": "...", "rationale": "...", "impact": "low|medium|high", "risk": "low|medium|high", "diff_hint": "Short hint which file(s) to change and what to add"}]
 - Focus on actionable, incremental improvements.
 - Prefer documentation + developer experience first if fundamentals are missing.
 - If objectives conflict, note in rationale.
 - Avoid duplicate or near-duplicate suggestions.

Objectives:
{objectives}

Repository Samples:
{samples}
""".strip()


def build_analysis_prompt(samples: List[Dict[str, Any]], objectives: List[str]) -> str:
    sample_lines = []
    for s in samples[:25]:  # additional guard
        snippet = s['snippet'].replace('```', '`\u200b``')
        sample_lines.append(f"### {s['path']} (bytes={s['size']})\n{snippet}\n")
    joined_samples = "\n".join(sample_lines)
    obj_text = "\n- " + "\n- ".join(objectives or ["(none specified)"])
    return ANALYZE_PROMPT_TEMPLATE.format(objectives=obj_text, samples=joined_samples)


class ChatClientLike(Protocol):
    def chat_completion(self, messages: List[Any]) -> str: ...

class SuggestionDict(TypedDict):
    id: str
    title: str
    rationale: str
    impact: str
    risk: str
    diff_hint: str

def analyze_repository(root: Path, objectives: List[str], client: ChatClientLike) -> List[SuggestionDict]:
    from .models import Message
    samples = collect_repo_sample(root)
    prompt = build_analysis_prompt(samples, objectives)
    msgs = [
        Message(role='system', content='You produce ONLY JSON arrays.'),
        Message(role='user', content=prompt)
    ]
    raw = client.chat_completion(msgs)
    # naive JSON extraction
    import json, re
    try:
        match = re.search(r'(\[.*\])', raw, re.S)
        if not match:
            return []
        parsed: Any = json.loads(match.group(1))
        if not isinstance(parsed, list):
            return []
        clean: List[SuggestionDict] = []
        for i, elem in enumerate(parsed, 1):
            if not isinstance(elem, dict):
                continue
            item: Dict[str, Any] = cast(Dict[str, Any], elem)
            cid_val = item.get('id') or f"s{i}"
            cid = str(cid_val)[:32]
            title = str(item.get('title', '') or '').strip()[:160]
            rationale = str(item.get('rationale', '') or '').strip()[:600]
            diff_hint = str(item.get('diff_hint', '') or '').strip()[:200]
            impact = str(item.get('impact', 'medium'))
            risk = str(item.get('risk', 'low'))
            clean.append(SuggestionDict(id=cid, title=title, rationale=rationale, impact=impact, risk=risk, diff_hint=diff_hint))
        return clean
    except Exception:
        pass
    return []


def build_unified_diff(path: str, original: str, new: str) -> str:
    import difflib
    a = original.splitlines()
    b = new.splitlines()
    diff = difflib.unified_diff(a, b, fromfile=path, tofile=path, lineterm='')
    return "\n".join(diff) + "\n"

# --- Diff Synthesis from diff_hint (heuristic) --- #
def _extract_first_path(diff_hint: str) -> str | None:
    import re
    # very loose pattern for paths ending with .py/.md/.txt
    m = re.search(r'([\w./-]+\.(?:py|md|txt))', diff_hint)
    if m:
        return m.group(1)
    return None

def synthesize_diff_from_hint(root: Path, title: str, rationale: str, diff_hint: str) -> Tuple[str, str]:
    """Return (target_path, diff_text).

    Heuristics:
    - Extract first plausible path token
    - If existing file: append a commented block referencing rationale
    - If not existing: create new file skeleton with rationale
    - Use build_unified_diff for unified diff output
    - Keep changes small & idempotent (skip if identical marker already present)
    """
    # sanitize
    safe_hint = (diff_hint or '').strip()[:400]
    rel = _extract_first_path(safe_hint) or 'IMPROVEMENTS.md'
    target = (root / rel).resolve()
    if not str(target).startswith(str(root.resolve())):
        rel = 'IMPROVEMENTS.md'
        target = root / rel
    existing = ''
    if target.exists():
        try:
            existing = target.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            existing = ''
    header_line = f"# AUTO-EVO: {title.strip()[:80]}".rstrip()
    marker = f"{header_line}"  # used to prevent duplication
    block_lines = [header_line,
                   f"# Rationale: {rationale.strip()[:160]}",
                   f"# Hint: {safe_hint}",
                   ""]
    if rel.endswith('.py'):
        pass  # comments already in block
    elif rel.endswith('.md') or rel.endswith('.txt'):
        # convert to markdown heading style
        block_lines = [marker.replace('# AUTO-EVO: ', '## '),
                       f"Rationale: {rationale.strip()[:160]}",
                       f"Hint: {safe_hint}",
                       ""]
    block = "\n".join(block_lines)
    if marker in existing:
        # nothing new; produce empty diff
        return rel, ''
    new_content = existing.rstrip() + "\n\n" + block if existing else block + "\n"
    diff = build_unified_diff(rel, existing, new_content)
    return rel, diff
