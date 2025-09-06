from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import json
from .models import WorldState, Message, PatchProposal
from .agents import EvolutionAgent, ScoringAgent
from .governance import ApprovalGate
from .diffing import apply_patch, validate_diff_security
from .logging_utils import JsonLogger, lightweight_changelog


class Orchestrator:
    """Coordinates proposal generation, scoring, approval and application."""

    def __init__(self, repo_root: Path, state_file: Optional[Path] = None) -> None:
        self.repo_root = repo_root
        self.state_file = state_file or (repo_root / ".evo_state.json")
        self.state = self._load_state()
        self.history: List[Message] = []
        # Agents / helpers
        self.evolution = EvolutionAgent(repo_root=self.repo_root)
        self.scoring = ScoringAgent()
        self.approvals = ApprovalGate()
        self.logger = JsonLogger(self.repo_root)

    # ---------------- Persistence ---------------- #
    def _load_state(self) -> WorldState:
        if self.state_file.exists():
            try:
                raw = json.loads(self.state_file.read_text(encoding="utf-8"))
                return WorldState(**raw)
            except Exception:  # pragma: no cover
                pass
        return WorldState(objectives=["Improve onboarding documentation."])

    def _save_state(self) -> None:
        try:
            self.state_file.write_text(json.dumps(self.state.__dict__, indent=2), encoding="utf-8")
        except Exception:  # pragma: no cover
            pass

    # ---------------- Main Cycle ---------------- #
    def cycle(self, dry_run: bool = True) -> List[PatchProposal]:
        proposals = self.evolution.propose(self.state)
        scored: List[PatchProposal] = []
        for p in proposals:
            p.score = self.scoring.score(p)
            self.approvals.submit(p)
            scored.append(p)
        self.state.cycle += 1
        self._save_state()
        self.logger.write({"event": "cycle", "cycle": self.state.cycle, "proposals": [p.id for p in scored]})
        return scored

    # ---------------- Introspection ---------------- #
    def list_pending(self) -> List[PatchProposal]:
        return self.approvals.list_pending()

    def approve(self, proposal_id: str) -> PatchProposal:
        return self.approvals.approve(proposal_id)

    # ---------------- Apply & Undo ---------------- #
    def apply_after_approval(self, proposal_id: str, dry_run: bool = True) -> str:
        proposal = self.approve(proposal_id)
        diff = proposal.diff
        validate_diff_security(diff)
        # --- Multi-File Diff Verarbeitung --- #
        segments: List[tuple[str, List[str]]] = []  # (filename, diff_lines)
        current_file: str | None = None
        current_lines: List[str] = []
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                if current_file is not None:
                    segments.append((current_file, current_lines))
                current_file = line[6:].strip()
                current_lines = []
                continue
            # ignore header markers and hunk headers, collect rest
            if line.startswith("--- a/"):
                continue
            if line.startswith("@@"):
                continue
            if current_file is not None:
                current_lines.append(line)
        if current_file is not None:
            segments.append((current_file, current_lines))
        if not segments:
            return "(kein Dateiinhalt im Diff / nichts anzuwenden)"

        from typing import Dict as _Dict, Any as _Any
        proposal_backups: List[_Dict[str, _Any]] = []
        applied_files: List[str] = []

        def _reconstruct(lines: List[str]) -> str:
            new_lines: List[str] = []
            for ln in lines:
                if ln.startswith("+") and not ln.startswith("+++"):
                    new_lines.append(ln[1:])
                elif ln.startswith("-"):
                    continue
                else:
                    new_lines.append(ln)
            return "\n".join(l.rstrip() for l in new_lines).strip() + "\n"

        import hashlib, time as _t
        version_log_path = self.repo_root / 'logs' / 'version_history.jsonl'
        version_log_path.parent.mkdir(exist_ok=True)
        if not dry_run:
            self.state.accepted_patches.append(proposal.id)
        for fname, lines in segments:
            target = self.repo_root / fname
            if target.exists():
                original = target.read_text(encoding='utf-8', errors='ignore')
                backup_path = self.repo_root / f".backup_{fname.replace('/', '_')}_{proposal.id}.txt"
                try:
                    backup_path.write_text(original, encoding='utf-8')
                except Exception:
                    pass
                proposal_backups.append({"file": fname, "path": str(backup_path), "original_exists": True})
            else:
                proposal_backups.append({"file": fname, "path": None, "original_exists": False})
            if not dry_run:
                new_content = _reconstruct(lines)
                apply_patch(self.repo_root, fname, new_content)
                sha = hashlib.sha256(new_content.encode('utf-8', errors='ignore')).hexdigest()[:16]
                # Version History Eintrag
                try:
                    with version_log_path.open('a', encoding='utf-8') as vf:
                        import json as _j
                        rec: _Dict[str, _Any] = {"ts": _t.time(), "proposal": proposal.id, "file": fname, "sha": sha, "cycle": self.state.cycle}
                        vf.write(_j.dumps(rec, ensure_ascii=False) + "\n")
                except Exception:
                    pass
                applied_files.append(fname)
        if not dry_run:
            self.state.backups[proposal.id] = proposal_backups
            if applied_files:
                lightweight_changelog(self.repo_root, f"Applied {proposal.id} -> {', '.join(applied_files)}")
            self.logger.write({"event": "apply", "id": proposal.id, "files": applied_files})
            self._save_state()
            return ', '.join(applied_files)
        return f"(dry-run) {', '.join(f for f,_ in segments)}"

    def preview(self, proposal_id: str) -> str:
        for p in self.approvals.pending:
            if p.id == proposal_id:
                return p.diff
        raise ValueError("Proposal not pending")

    def undo_last(self) -> Optional[str]:
        if not self.state.accepted_patches:
            return None
        last = self.state.accepted_patches.pop()
        bkp_list = self.state.backups.get(last, [])
        for info in bkp_list:
            file = info.get("file")
            path = info.get("path")
            if info.get("original_exists") and path:
                try:
                    original_content = Path(path).read_text(encoding="utf-8")
                    if isinstance(file, str):
                        apply_patch(self.repo_root, file, original_content)
                except Exception:
                    pass
            else:
                try:
                    if file:
                        pth = self.repo_root / file
                        if pth.exists():
                            pth.unlink()
                except Exception:
                    pass
        self.state.backups.pop(last, None)
        self._save_state()
        self.logger.write({"event": "undo", "id": last})
        return last
