<!-- Quickstart Enhancement Start -->
## Schneller Überblick (Einfach)
1. Umgebung anlegen: `python -m venv .venv`
2. Aktivieren (PowerShell): `./.venv/Scripts/Activate.ps1`
3. Abhängigkeiten: `pip install -r requirements.txt`
4. Env vorbereiten: `copy .env.example .env` und Key eintragen
5. Simulation (Test): `python run_simulation.py --cycles 1 --dry-run`
6. Vorschlag anwenden: `python run_simulation.py --apply p1 --cycles 1`
7. API starten: `uvicorn src.api.app:app --port 8099`
8. Browser: http://127.0.0.1:8099 (Frontend optional wenn vorhanden)

## API Endpunkte (Kurz)
| Methode | Pfad | Zweck |
|---------|------|-------|
| POST | /cycle | Neue Vorschläge (dry) |
| GET  | /pending | Offene Vorschläge |
| POST | /apply/{id} | Anwenden |
| POST | /undo | Letztes rückgängig |
| GET  | /health | Status |

<!-- Quickstart Enhancement End -->

## Groq Nutzung
Standardmodell (automatisch): `gemma2-9b-it` – wurde per Scan als funktionierend erkannt.

Eigene Modelle testen:
```
python run_simulation.py --groq-scan
python run_simulation.py --groq-model gemma2-9b-it --groq-raw-call "Kurzer Test"
```

Benchmark Script (Antwort-Latenz & Erfolg):
```
python scripts/groq_benchmark.py --prompt "Kurzer Benchmark Satz." --output benchmark_result.json
```

Falls ein Modell 404 oder 400 liefert, ist es für den aktuellen Key nicht freigeschaltet.

## LLM Proposals aktivieren
Sobald ein gültiger Key in `.env` steht, generiert der EvolutionAgent zuerst Groq-basierte Vorschläge (JSON), sonst Fallback.

## Twin / Klon System (Neu)
Das System kann jetzt einen Sandbox-Klon (System B) der aktuellen Codebasis erstellen, dort Evolutionszyklen isoliert durchführen und anschließend Änderungen selektiv zurück in das Hauptsystem (System A) promoten.

### Fähigkeiten
- Sandbox-Evolution: Risikoarme Experimente ohne Hauptcode direkt zu verändern.
- Geänderte Dateien erkennen: Hash-basierter Vergleich gegen Baseline.
- Promotion: Kopiert nur veränderte, erlaubte Dateien zurück.
- Snapshots: Zeitreise / Revert auf frühere Zustände (leichtgewichtig, nur relevante Dateitypen).
- Platzsparend: Ignoriert `.git`, `.venv`, `logs`, Cache-Verzeichnisse.
- Wiederholbarkeit: Schnell Sandbox resetten und erneut experimentieren.

### CLI Befehle (Beispiele)
Sandbox Zyklen (nur Vorschläge sammeln, kein Anwenden):
```
python run_simulation.py --twin-sandbox-cycle 2 --dry-run
```

Geänderte Dateien im Sandbox-Klon anzeigen:
```
python run_simulation.py --twin-list-changed
```

Änderungen promoten (Standard: alle veränderten Dateien):
```
python run_simulation.py --twin-promote --dry-run   # zeigt nur an
python run_simulation.py --twin-promote            # wendet an
```

Snapshot erstellen / anzeigen / wiederherstellen:
```
python run_simulation.py --snapshot-create "Vor Twin Experiment"
python run_simulation.py --snapshot-list
python run_simulation.py --snapshot-restore s1
```

### Dateistruktur intern
```
.twin/
	sandbox/        # Arbeitskopie für System B
	snapshots/      # Snapshot Verzeichnisse (s1/, s2/, ...)
	meta.json       # Metadaten der Snapshots
```

### Nutzen & Mehrwert
- Sicherheitsnetz: Hauptsystem bleibt stabil bis Promotion.
- Evolutions-Hygiene: Reduzierte Gefahr von inkonsistenten Zwischenständen.
- Schnelle Iteration: Mehrere Sandbox-Zyklen bevor etwas zurückfließt.
- Auditierbarkeit: Snapshots + Promotion-Listen erlauben Nachvollziehbarkeit.
- Unterhaltung / Exploratives Arbeiten: Man kann "Was-wäre-wenn"-Mutationen laufen lassen und später entscheiden.

### Geplante Erweiterungen (Ideen)
- Health Checks vor Promotion (Tests / Lint / Build).
- Selektive Promotion via Include/Exclude Pattern.
- Automatischer Promotionschwellenwert (z.B. nach X erfolgreichen Sandbox-Zyklen).
- Bidirektionale Kommunikation (Feedback Kanal: Hauptsystem -> Sandbox Strategie).
- Controller-Agent der Sandbox-Strategien vorgibt (Refactoring vs. Docs vs. Tests).

## Unterhaltung & Kreativer Einsatz
- "Time Machine Coding": Mehrere Varianten erzeugen, Snapshots vergleichen, beste Variante promoten.
- "Sandbox Battles": Zwei getrennte Sandboxen gegeneinander Ideen generieren lassen (zukünftig erweiterbar).
- "Evolving Docs": Nur Dokumentationsdateien mutieren lassen und schauen, wie sich Onboarding-Texte verbessern.
- "Refactor Sprints": Mehrere Zyklen mit Fokus auf Lesbarkeit sammeln, dann alles prüfen und selektiv promoten.

## Kurze Referenz aller neuen Flags
| Flag | Zweck |
|------|-------|
| --twin-sandbox-cycle N | Führt N Evolutionszyklen im Sandbox-Klon aus |
| --twin-list-changed | Listet geänderte Dateien der Sandbox |
| --twin-promote | Promotet Änderungen (Kopie zurück) |
| --snapshot-create LABEL | Erstellt Snapshot mit Label |
| --snapshot-list | Listet vorhandene Snapshots |
| --snapshot-restore ID | Stellt Snapshot wieder her |

Hinweis: Kombination mit `--dry-run` zeigt sichere Vorschauen.

## Ziele / Objectives & Analyse (Neu)
Du kannst jetzt Zielsetzungen vorgeben, die der Analyse-/Vorschlagsprozess berücksichtigt.

Workflow grob:
1. Ziele setzen (UI Panel oder API `POST /objectives`).
2. Analyse starten (`GET /analyze`). Liefert strukturierte Vorschläge (id, title, rationale, diff_hint).
3. Einzelnen Vorschlag in Pending-Queue schieben (`POST /inject-proposal`).

Beispiel (API):
```
curl -X POST http://127.0.0.1:8099/objectives -H "Content-Type: application/json" -d '{"objectives":["Dokumentation verbessern","Tests erhöhen"]}'
curl http://127.0.0.1:8099/analyze
curl -X POST http://127.0.0.1:8099/inject-proposal -H "Content-Type: application/json" -d '{"title":"Docs Ergänzung","rationale":"Mehr README Hinweise","diff":"--- a/README.md\n+++ b/README.md\n@@\n+# TODO: neue Infos\n"}'
```

Diff-Hinweise aus der Analyse können als Startpunkt dienen; echte Diffs entstehen weiter durch den Evolutionsprozess oder manuell.

## Chat Interface (Neu)
Ein leichter kontextueller Chat ist integriert:
* Endpunkte: `POST /chat`, `GET /chat/history`
* Verlauf wird persistiert unter `logs/chat_history.json` (rotierend auf 50 Einträge).
* System-Prompt enthält aktuell gesetzte Objectives → Antworten richten sich stärker an deine Ziele.

### Chat → Proposal Injection
Letzte (oder angegebene) Assistant-Antwort kann direkt als Pending-Vorschlag gespeichert werden.

Endpoint:
```
POST /chat/to-proposal
Body: {"index": <optional assistant index>, "filename": "README.md"}
```
Rückgabe:
```
{ "injected": true, "proposal_id": "chat173..." }
```
Die erzeugte Diff ist bewusst konservativ (Kommentar-Diff) und dient als Platzhalter – du kannst den Vorschlag normal previewen & anpassen.

UI: Button "→ Proposal" im Chat-Panel konvertiert automatisch die letzte Assistant-Antwort.

## Erweiterte API Übersicht (Aktualisiert)
| Methode | Pfad | Zweck |
|---------|------|-------|
| GET | / | Single-Page UI |
| GET | /meta | Metadaten (Cycle, Pending, Model, Snapshots, Chat Count) |
| POST | /cycle | Evolution – neuen Satz Vorschläge generieren (dry) |
| GET | /pending | Pending Vorschläge auflisten |
| POST | /apply/{id} | Vorschlag anwenden (inkl. Score Governance) |
| POST | /undo | Letzte Anwendung rückgängig |
| GET | /preview/{id} | Unified Diff eines Vorschlags |
| GET | /health | Basis-Health |
| GET | /groq-check | Testet Groq-Verfügbarkeit |
| POST | /llm/raw | Rohprompt an aktuelles Modell |
| POST | /objectives | Ziele setzen |
| GET | /analyze | Repo + Ziele analysieren, Ideen liefern |
| POST | /inject-proposal | Analyse/Manuelle Idee in Pending bringen |
| POST | /chat | Chat Nachricht senden |
| GET | /chat/history | Kompletter Chat-Verlauf |
| POST | /chat/to-proposal | Letzte (oder indexierte) Assistant-Antwort → Proposal |
| POST | /twin/sandbox-cycle | Sandbox Evolutionszyklen |
| GET | /twin/changed | Geänderte Dateien im Sandbox-Klon |
| POST | /twin/promote | Änderungen promoten |
| POST | /twin/reset | Sandbox zurücksetzen |
| POST | /snapshot/create | Snapshot speichern |
| GET | /snapshot/list | Snapshot-Liste |
| POST | /snapshot/restore/{id} | Snapshot wiederherstellen |

## Nächste mögliche Ausbauten
| Idee | Nutzen |
|------|-------|
| Automatische Analyse nach jedem akzeptierten Patch | Kontinuierliche Ziel-Refresher |
| Diff-Synthese aus `diff_hint` (Dateien lesen & patchen) | Schnellere vollständige Vorschläge |
| Health Gate vor Promotion (Tests/Lint) | Qualitätssicherung |
| Mehrere parallele Sandboxen | Strategievergleich |
| Persistente Chat-Labels / Tags | Semantische Historie |

---
Stand: Chat + Objectives + Analyse + Twin/Snapshots integriert. Weitere Wünsche einfach posten.

