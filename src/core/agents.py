from __future__ import annotations
from typing import List, Optional, Any, Dict
from pathlib import Path
from .models import Message, PatchProposal, Score, WorldState
from ..io.groq_client import GroqClient
import uuid

class BaseAgent:
    name: str = "agent"
    role: str = "agent"

    def system_prompt(self) -> str:
        return "You are a structured assistant agent. Keep output concise."

    def make_messages(self, history: List[Message]) -> List[Message]:
        return history

class EvolutionAgent(BaseAgent):
    name = "evolution"

    def __init__(self, repo_root: Optional[Path] = None) -> None:
        self.client: Optional[GroqClient] = None
        self.repo_root = repo_root
        try:
            self.client = GroqClient()
        except Exception:  # pragma: no cover
            self.client = None

    def _fallback_static(self, state: WorldState) -> List[PatchProposal]:
        """Cycle-adaptive simple proposals so we don't repeat endlessly."""
        base: List[PatchProposal] = []
        c = state.cycle
        if c == 0:
            base.append(PatchProposal(
                id="p1",
                title="Add CONTRIBUTING guide",
                description="Plain & friendly contributing basics.",
                diff="""--- a/CONTRIBUTING.md\n+++ b/CONTRIBUTING.md\n+# Contributing\n+Danke, dass du helfen möchtest!\n+## Schritte\n+1. Issue aufmachen\n+2. Branch erstellen\n+3. Änderung committen\n+4. Pull Request stellen\n+## Stil\n+Kurze Commits, beschreibende Titel.\n""",
                rationale="Lower barrier for new helpers.",
                risk_note="Low"
            ))
        if c <= 1:
            base.append(PatchProposal(
                id="p2",
                title="Improve README Quickstart",
                description="Add clearer bullet list + API + Web UI info.",
                diff="""--- a/README.md\n+++ b/README.md\n+<!-- Quickstart Enhancement Start -->\n+## Schneller Überblick (Einfach)\n+1. Umgebung anlegen: `python -m venv .venv`\n+2. Aktivieren (PowerShell): `./.venv/Scripts/Activate.ps1`\n+3. Abhängigkeiten: `pip install -r requirements.txt`\n+4. Env vorbereiten: `copy .env.example .env` und Key eintragen\n+5. Simulation (Test): `python run_simulation.py --cycles 1 --dry-run`\n+6. Vorschlag anwenden: `python run_simulation.py --apply p1 --cycles 1`\n+7. API starten: `uvicorn src.api.app:app --port 8099`\n+8. Browser: http://127.0.0.1:8099 (Frontend optional wenn vorhanden)\n+\n+## API Endpunkte (Kurz)\n+| Methode | Pfad | Zweck |\n+|---------|------|-------|\n+| POST | /cycle | Neue Vorschläge (dry) |\n+| GET  | /pending | Offene Vorschläge |\n+| POST | /apply/{id} | Anwenden |\n+| POST | /undo | Letztes rückgängig |\n+| GET  | /health | Status |\n+\n+<!-- Quickstart Enhancement End -->\n""",
                rationale="First impression matters.",
                risk_note="Low"
            ))
        if c == 2:
            base.append(PatchProposal(
                id="p_arch",
                title="Add ARCHITECTURE overview",
                description="Basic architecture explanation for agents & orchestrator.",
                diff="""--- a/ARCHITECTURE.md\n+++ b/ARCHITECTURE.md\n+# Architekturüberblick\n+Dieses Projekt nutzt einen Orchestrator, der Evolutions- und Scoring-Agenten koordiniert.\n+## Komponenten\n+- Orchestrator: Ablauf & Persistenz\n+- EvolutionAgent: erzeugt Vorschläge (Fallback oder Groq)\n+- ScoringAgent: heuristische Bewertung\n+- Governance: Policy-Prüfungen (Basis)\n+- Logging: JSONL + Changelog\n+## Zyklus\n+Proposals -> Scoring -> Pending -> Apply (Backup + Undo)\n""",
                rationale="Helps contributors grasp system quickly.",
                risk_note="Low"
            ))
        if c == 3:
            base.append(PatchProposal(
                id="p_roadmap",
                title="Add ROADMAP",
                description="Outline future improvements and priorities.",
                diff="""--- a/ROADMAP.md\n+++ b/ROADMAP.md\n+# Roadmap\n+## Kurzfristig\n+- Verbesserte Tests (Apply/Undo)\n+- LLM Output Validierung erweitern\n+## Mittelfristig\n+- Embedding Retrieval\n+- Risiko/Effort Modellierung verbessern\n+## Langfristig\n+- Multi-Agent Reflexionsschleifen\n+- Auth & Security Härtung (API)\n""",
                rationale="Signals direction for contributors.",
                risk_note="Low"
            ))
        if c == 4:
            base.append(PatchProposal(
                id="p_testing",
                title="Add TESTING guide",
                description="Explain how to run and extend tests.",
                diff="""--- a/TESTING.md\n+++ b/TESTING.md\n+# Testing Leitfaden\n+## Ausführen\n+pytest -q\n+## Was testen?\n+- Scoring Edge Cases\n+- Apply/Undo Roundtrip\n+- Diff Sicherheits-Validierung\n+## Nächste Schritte\n+- Property Tests für Patch Parser\n+""",
                rationale="Encourages test culture.",
                risk_note="Low"
            ))
        return base

    def propose(self, state: WorldState) -> List[PatchProposal]:
        if not self.client or not self.client.cfg.api_key:
            # Log no-key situation for transparency
            try:  # pragma: no cover
                if self.repo_root:
                    logs_dir = self.repo_root / "logs"
                    logs_dir.mkdir(exist_ok=True)
                    (logs_dir / "groq_raw_last.txt").write_text("[no-key]", encoding="utf-8")
            except Exception:
                pass
            return self._fallback_static(state)
        prompt = (
            "Erzeuge bis zu 3 kleine, sichere Verbesserungsvorschläge für dieses Projekt. "
            "Nutze folgendes Format JSON: [{id,title,description,rationale,filename,content}]. "
            "Fokussiere auf Doku oder kleine Hilfsdateien. Keine gefährlichen Operationen."
        )
        messages = [Message(role="system", content="Du bist ein hilfreicher Verbesserungsagent."),
                    Message(role="user", content=prompt)]
        raw = self.client.chat_completion(messages)
        # Rohantwort optional loggen
        try:  # pragma: no cover
            if self.repo_root:
                logs_dir = self.repo_root / "logs"
                logs_dir.mkdir(exist_ok=True)
                (logs_dir / "groq_raw_last.txt").write_text(raw, encoding="utf-8")
        except Exception:
            pass
        import json
        # Versuche robust JSON zu extrahieren, falls das Modell zusätzlichen Text liefert
        def _extract_json_block(txt: str) -> str:
            txt = txt.strip()
            if txt.startswith("[") and txt.endswith("]"):
                return txt
            # Suche ersten '[' und passenden schließenden ']'
            start = txt.find("[")
            if start == -1:
                return txt
            # Primitive Balance-Suche
            depth = 0
            for i, ch in enumerate(txt[start:], start):
                if ch == '[':
                    depth += 1
                elif ch == ']':
                    depth -= 1
                    if depth == 0:
                        return txt[start:i+1]
            return txt
        raw_json = _extract_json_block(raw)
        proposals: List[PatchProposal] = []
        try:
            parsed_any: Any = json.loads(raw_json)
            data_list: List[Dict[str, Any]] = parsed_any if isinstance(parsed_any, list) else []
            for raw_obj in data_list[:3]:
                if not isinstance(raw_obj, dict):
                    continue
                filename = str(raw_obj.get("filename", "IMPROVEMENT.md"))
                content = str(raw_obj.get("content", "Placeholder"))
                diff_body_lines: List[str] = [str(line) for line in content.splitlines()]
                diff = f"--- a/{filename}\n+++ b/{filename}\n+" + "\n+".join(diff_body_lines) + "\n"
                proposals.append(PatchProposal(
                    id=str(raw_obj.get("id", "p-" + uuid.uuid4().hex[:6]))[:24],
                    title=str(raw_obj.get("title", "Untitled"))[:120],
                    description=str(raw_obj.get("description", ""))[:400],
                    diff=diff,
                    rationale=str(raw_obj.get("rationale", ""))[:300],
                    risk_note="Low"
                ))
        except Exception:
            return self._fallback_static(state)
        return proposals or self._fallback_static(state)

class ScoringAgent(BaseAgent):
    name = "scoring"

    def score(self, proposal: PatchProposal) -> Score:
        # Very naive static scoring placeholder
        base = len(proposal.description)
        return Score(
            clarity=0.8,
            impact=0.6 + min(0.3, base / 200),
            risk=0.2,
            effort=0.3,
            meta={"heuristic": True}
        )
