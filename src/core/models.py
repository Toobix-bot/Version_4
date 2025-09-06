from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import time

# ---- Data Structures ---- #

@dataclass
class Message:
    role: str  # system | user | assistant | agent
    content: str
    timestamp: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PatchProposal:
    id: str
    title: str
    description: str
    diff: str  # unified diff (initially maybe placeholder)
    rationale: str
    risk_note: str = ""
    created_at: float = field(default_factory=time.time)
    score: Optional["Score"] = None

@dataclass
class Score:
    clarity: float
    impact: float
    risk: float
    effort: float
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def composite(self) -> float:
        # Simple heuristic: (clarity + impact) - (risk + effort)/2
        return (self.clarity + self.impact) - (self.risk + self.effort) / 2

@dataclass
class WorldState:
    objectives: List[str] = field(default_factory=list)
    accepted_patches: List[str] = field(default_factory=list)
    cycle: int = 0
    notes: Dict[str, Any] = field(default_factory=dict)
    # Mapping proposal_id -> list of backup records {file, path, original_exists}
    backups: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

@dataclass
class MemoryItem:
    kind: str  # short|mid|long
    content: str
    tags: List[str] = field(default_factory=list)
    importance: float = 0.5
    created_at: float = field(default_factory=time.time)

@dataclass
class RetrievalResult:
    ref: str
    snippet: str
    score: float

# ---- Exceptions ---- #

class GovernanceError(Exception):
    pass

class PolicyViolation(GovernanceError):
    pass
