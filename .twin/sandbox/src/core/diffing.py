from __future__ import annotations
from difflib import unified_diff
from pathlib import Path
from typing import Iterable


def make_diff(original: str, modified: str, filename: str) -> str:
    a = original.splitlines(keepends=True)
    b = modified.splitlines(keepends=True)
    diff = unified_diff(a, b, fromfile=f"a/{filename}", tofile=f"b/{filename}")
    return "".join(diff)


def apply_patch(root: Path, filename: str, new_content: str) -> None:
    target = root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content, encoding='utf-8')


def validate_diff_security(diff: str) -> None:
    forbidden = ["/etc/", "rm -rf", "Invoke-WebRequest"]
    lowered = diff.lower()
    for token in forbidden:
        if token.lower() in lowered:
            raise ValueError(f"Potentially dangerous content in diff: {token}")


def extract_touched_files(diff: str) -> Iterable[str]:
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            yield line[6:]
