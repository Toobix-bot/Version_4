from __future__ import annotations
from typing import List
from .models import PatchProposal, PolicyViolation

APPROVAL_REQUIRED = True

SAFE_PATTERNS = [
    "README.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "*.md",
    "*.txt",
    "*.json",
]

DENY_PATTERNS = [
    "requirements.txt",  # nur explizit
    ".env",  # secrets
    "*.pyc",
    "__pycache__",
]

def _match(pattern: str, filename: str) -> bool:
    if pattern == filename:
        return True
    if pattern.startswith("*."):
        return filename.endswith(pattern[1:])
    return False

def _is_safe(filename: str) -> bool:
    return any(_match(p, filename) for p in SAFE_PATTERNS) and not any(_match(d, filename) for d in DENY_PATTERNS)

def policy_check(proposal: PatchProposal) -> None:
    # crude parse touched files
    touched = []
    for line in proposal.diff.splitlines():
        if line.startswith("+++ b/"):
            touched.append(line[6:])
    # If no files, ignore
    for f in touched:
        if not _is_safe(f):
            raise PolicyViolation(f"File '{f}' not allowed by policy.")

class ApprovalGate:
    def __init__(self) -> None:
        self.pending: List[PatchProposal] = []

    def submit(self, proposal: PatchProposal) -> None:
        policy_check(proposal)
        self.pending.append(proposal)

    def list_pending(self) -> List[PatchProposal]:
        return list(self.pending)

    def approve(self, proposal_id: str) -> PatchProposal:
        for i, p in enumerate(self.pending):
            if p.id == proposal_id:
                return self.pending.pop(i)
        raise ValueError("Proposal not found")
