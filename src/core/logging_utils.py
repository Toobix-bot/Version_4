from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from typing import Any, Dict

class JsonLogger:
    def __init__(self, root: Path, name: str = "evo_log") -> None:
        self.dir = root / "logs"
        self.dir.mkdir(exist_ok=True)
        self.base = self.dir / f"{name}.jsonl"

    def write(self, record: Dict[str, Any]) -> None:
        line = json.dumps({"ts": datetime.utcnow().isoformat() + "Z", **record}, ensure_ascii=False)
        with self.base.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

def lightweight_changelog(root: Path, message: str) -> None:
    file = root / "CHANGELOG.md"
    if not file.exists():
        file.write_text("# Changelog\n\n", encoding="utf-8")
    with file.open("a", encoding="utf-8") as f:
        f.write(f"- {message}\n")