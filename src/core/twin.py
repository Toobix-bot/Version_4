from __future__ import annotations
"""Twin / Klon Architektur

Dieses Modul stellt zwei zentrale Bausteine bereit:

1. SnapshotManager
   - Erstellt platzsparende Snapshots ausgewählter Dateitypen (Quellcode + Docs)
   - Listet und stellt Snapshots wieder her (Zeitreise / Revert)

2. TwinCoordinator
   - Erzeugt / verwaltet einen Sandbox-Klon ("System B") im Verzeichnis .twin/sandbox
   - Führt Evolutionszyklen nur im Sandbox-Klon aus
   - Ermittelt geänderte Dateien (gegenüber Haupt-Repo) und kann diese promoten
   - Kommunikation aktuell simpel: Promotion kopiert Dateien zurück (einseitig A<-B)

Design-Ziele:
 - Minimal-invasiv: Keine Änderung am bestehenden Orchestrator nötig
 - Platzsparend: Ignoriere große / volatile Verzeichnisse (.git, .venv, logs, __pycache__, .twin)
 - Reversibel: Schnelle Wiederherstellung durch Snapshots
 - Erweiterbar: Spätere HealthChecks / Multi-Richtungs-Kommunikation möglich
"""

from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Iterable
import shutil
import hashlib
import json
import time

ALLOWED_EXT = {".py", ".md", ".txt", ".json", ".yml", ".yaml"}
IGNORE_DIRS = {".git", ".venv", "__pycache__", "logs", ".twin"}


def _hash_file(p: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(p.read_bytes())
        return h.hexdigest()
    except Exception:
        return "-"


def _iter_files(base: Path) -> Iterable[Path]:
    for p in base.rglob("*"):
        if p.is_file():
            rel_parts = p.relative_to(base).parts
            if any(part in IGNORE_DIRS for part in rel_parts):
                continue
            if p.suffix in ALLOWED_EXT:
                yield p


@dataclass
class SnapshotMeta:
    id: str
    label: str
    timestamp: float
    path: str


class SnapshotManager:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.twin_dir = repo_root / ".twin"
        self.snap_dir = self.twin_dir / "snapshots"
        self.meta_file = self.twin_dir / "meta.json"
        self.twin_dir.mkdir(exist_ok=True)
        self.snap_dir.mkdir(exist_ok=True)
        if not self.meta_file.exists():
            self.meta_file.write_text(json.dumps({"snapshots": []}, indent=2), encoding="utf-8")

    # ---------- intern ---------- #
    def _load_all(self) -> List[SnapshotMeta]:
        try:
            raw = json.loads(self.meta_file.read_text(encoding="utf-8"))
            return [SnapshotMeta(**d) for d in raw.get("snapshots", [])]
        except Exception:
            return []

    def _save_all(self, snaps: List[SnapshotMeta]) -> None:
        data = {"snapshots": [asdict(s) for s in snaps]}
        try:
            self.meta_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ---------- API ---------- #
    def list(self) -> List[SnapshotMeta]:
        return self._load_all()

    def create(self, label: str) -> SnapshotMeta:
        snaps = self._load_all()
        next_idx = len(snaps) + 1
        snap_id = f"s{next_idx}"
        folder = self.snap_dir / snap_id
        folder.mkdir(parents=True, exist_ok=True)
        # Kopiere erlaubte Dateien (flach strukturgetreu)
        for f in _iter_files(self.repo_root):
            rel = f.relative_to(self.repo_root)
            target = folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(f, target)
            except Exception:
                pass
        meta = SnapshotMeta(id=snap_id, label=label, timestamp=time.time(), path=str(folder))
        snaps.append(meta)
        self._save_all(snaps)
        return meta

    def restore(self, snap_id: str) -> bool:
        snaps = self._load_all()
        match = next((s for s in snaps if s.id == snap_id), None)
        if not match:
            return False
        src = Path(match.path)
        if not src.exists():
            return False
        # Rückkopieren (nur erlaubte Dateitypen, vorhandene überschreiben)
        for f in _iter_files(src):
            rel = f.relative_to(src)
            target = self.repo_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(f, target)
            except Exception:
                pass
        return True


class TwinCoordinator:
    """Verwaltet Sandbox-Klon und Promotion von Änderungen."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.twin_dir = repo_root / ".twin"
        self.sandbox_dir = self.twin_dir / "sandbox"
        self.baseline_hash_file = self.sandbox_dir / ".baseline_hashes.json"
        self.twin_dir.mkdir(exist_ok=True)

    # ---------- Sandbox Management ---------- #
    def init_sandbox(self, force: bool = False) -> Path:
        if self.sandbox_dir.exists() and not force:
            return self.sandbox_dir
        if self.sandbox_dir.exists() and force:
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)
        for f in _iter_files(self.repo_root):
            rel = f.relative_to(self.repo_root)
            target = self.sandbox_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(f, target)
            except Exception:
                pass
        # baseline Hashes
        hashes = {str(p.relative_to(self.sandbox_dir)): _hash_file(p) for p in _iter_files(self.sandbox_dir)}
        self.baseline_hash_file.write_text(json.dumps(hashes, indent=2), encoding="utf-8")
        return self.sandbox_dir

    def _load_baseline(self) -> Dict[str, str]:
        if self.baseline_hash_file.exists():
            try:
                return json.loads(self.baseline_hash_file.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    # ---------- Evolution in Sandbox ---------- #
    def sandbox_cycle(self, cycles: int = 1, dry_run: bool = True) -> List[str]:
        from .orchestrator import Orchestrator
        self.init_sandbox()
        orch = Orchestrator(repo_root=self.sandbox_dir)
        produced: List[str] = []
        for _ in range(cycles):
            props = orch.cycle(dry_run=dry_run)
            produced.extend([p.id for p in props])
        return produced

    # ---------- Promotion ---------- #
    def diff_changed_files(self) -> List[str]:
        self.init_sandbox()
        changed: List[str] = []
        baseline = self._load_baseline()
        for f in _iter_files(self.sandbox_dir):
            rel = str(f.relative_to(self.sandbox_dir))
            h_now = _hash_file(f)
            h_base = baseline.get(rel)
            if h_base is None:
                changed.append(rel)
            elif h_base != h_now:
                changed.append(rel)
        return sorted(changed)

    def promote(self, files: Optional[List[str]] = None, dry_run: bool = True) -> List[str]:
        self.init_sandbox()
        changed = self.diff_changed_files()
        if files:
            selected = [f for f in changed if f in files]
        else:
            selected = changed
        promoted: List[str] = []
        for rel in selected:
            src = self.sandbox_dir / rel
            dst = self.repo_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dry_run:
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    continue
            promoted.append(rel + (" (dry)" if dry_run else ""))
        return promoted

    # ---------- Utility ---------- #
    def reset_sandbox(self) -> None:
        if self.sandbox_dir.exists():
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)
        self.init_sandbox(force=True)
