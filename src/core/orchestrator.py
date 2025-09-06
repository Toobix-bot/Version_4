from __future__ import annotations
from pathlib import Path
from typing import List, Optional
import json
from .models import WorldState, Message, PatchProposal
from .agents import EvolutionAgent, ScoringAgent
from .governance import ApprovalGate
from .diffing import apply_patch, validate_diff_security, extract_touched_files
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
        touched = list(extract_touched_files(diff))
        if not touched:
            return "(kein Dateiinhalt im Diff / nichts anzuwenden)"
        filename = touched[0]
        new_lines: List[str] = []
        for line in diff.splitlines():
            if line.startswith("+++ ") or line.startswith("--- "):
                continue
            if line.startswith("@@"):
                continue
            if line.startswith("+") and not line.startswith("+++"):
                new_lines.append(line[1:])
            elif line.startswith("-"):
                continue
            else:
                new_lines.append(line)
        new_content = "\n".join(l.rstrip() for l in new_lines).strip() + "\n"
        # Backup handling
        proposal_backups = []
        target = self.repo_root / filename
        if target.exists():
            original = target.read_text(encoding="utf-8", errors="ignore")
            backup_path = self.repo_root / f".backup_{filename.replace('/', '_')}_{proposal.id}.txt"
            backup_path.write_text(original, encoding="utf-8")
            proposal_backups.append({"file": filename, "path": str(backup_path), "original_exists": True})
        else:
            proposal_backups.append({"file": filename, "path": None, "original_exists": False})
        if not dry_run:
            apply_patch(self.repo_root, filename, new_content)
            self.state.accepted_patches.append(proposal.id)
            self.state.backups[proposal.id] = proposal_backups
            lightweight_changelog(self.repo_root, f"Applied {proposal.id} -> {filename}")
            self.logger.write({"event": "apply", "id": proposal.id, "file": filename})
            self._save_state()
            return filename
        return f"(dry-run) {filename}"

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
