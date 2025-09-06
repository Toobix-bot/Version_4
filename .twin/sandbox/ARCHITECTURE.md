# Architekturüberblick
Dieses Projekt nutzt einen Orchestrator, der Evolutions- und Scoring-Agenten koordiniert.
## Komponenten
- Orchestrator: Ablauf & Persistenz
- EvolutionAgent: erzeugt Vorschläge (Fallback oder Groq)
- ScoringAgent: heuristische Bewertung
- Governance: Policy-Prüfungen (Basis)
- Logging: JSONL + Changelog
## Zyklus
Proposals -> Scoring -> Pending -> Apply (Backup + Undo)
