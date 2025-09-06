## Überblick
Dieses Projekt ist ein autonomer Evolutions- und Refactoring-Assistent für ein Code-Repository. Er generiert Verbesserungsvorschläge (Proposals) mittels LLM, bewertet sie, erlaubt Sandbox-Experimente in einem Klon (Twin), verwaltet Snapshots, berücksichtigt definierte Ziele (Objectives) und bietet ein Chat-Interface zur interaktiven Steuerung.

### Variante: go (Konfigurationsentscheidungen)
Diese laufende Instanz folgt einer fokussierten Variante ("go") mit bewusst reduziertem Funktionsumfang für Klarheit & Testbarkeit:
* Aktivierte Feature-Kategorien: System, Ziele, Analyse, World, Improve, Knowledge
* Entfernte/Deaktivierte Kategorien (vorerst): Personas, Multi, Reflexion (außer memory.compress intern weiter verfügbar), User, Notebook, Energy, Self, Coach
* Analyse-Limits: max. 3s Laufzeit / 100 Dateien Sampling-Grenze
* Welt-Standardgröße: 40x24 ( `/world.init` ohne Argumente erzeugt 40x24 )
* Ressourcenmodell (World Entities): energy, knowledge, material, exp
* Autonomiegrenze KI: liefert nur Regel-VORSCHLÄGE (keine automatischen Regeländerungen)
* Zielmetriken (Placeholder v1): a, b, c, d
* Marker Modul: `src/core/variant.py` (zentraler Single-Source für VariantConfig)
* Rate Limiting (einfach): `RATE_LIMIT_PER_MIN` (Default 120) – global + per-IP Soft-Limit
* Strukturierte Fehler: globaler Exception-Handler liefert JSON `{error, detail, path}`

Zweck: Schnell stabile Kern-Loops (Objectives → Analyze → Proposal → Apply) + einfache Simulation etablieren, bevor komplexe soziale / multi-agent Features wieder aktiviert werden.

## Inhaltsverzeichnis
1. Überblick
2. Schneller Überblick (Quickstart)
3. Voraussetzungen & Environment
4. Architektur (Kurz)
5. Twin / Sandbox Konzept
6. Ziele & Analyse
7. Chat Interface
8. Erweiterte API Übersicht
9. Tests & Qualität (geplant)
10. Troubleshooting
11. Contribution Guide
12. Glossar
13. Roadmap / Nächste Ausbauten

<!-- Quickstart Enhancement Start -->
## Schneller Überblick (Quickstart)
1. Umgebung anlegen: `python -m venv .venv`
2. Aktivieren (PowerShell): `./.venv/Scripts/Activate.ps1`
3. Abhängigkeiten: `pip install -r requirements.txt`
4. Env vorbereiten: `copy .env.example .env` und Key eintragen
5. Simulation (Test): `python run_simulation.py --cycles 1 --dry-run`
6. Vorschlag anwenden: `python run_simulation.py --apply p1 --cycles 1`
7. API starten: `uvicorn src.api.app:app --port 8099`
8. Browser: http://127.0.0.1:8099 (Frontend optional wenn vorhanden)

## Onboarding (Erste Schritte)
Dieser Abschnitt führt dich in den ersten 10 Minuten zum ersten erfolgreichen Verbesserungsvorschlag.

### 1. Umgebung bereitstellen
Siehe Quickstart Schritte 1–4. Prüfe mit:
```
python run_simulation.py --cycles 0 --dry-run
```
Wenn ohne Fehler: Basissetup ok.

### 2. Ziele (Objectives) setzen
Lege 1–3 klare Ziele fest (kurz & prägnant). Beispiele:
```
Dokumentation verbessern
Tests erhöhen
Refactoring der Chat-Logik
```
UI: Ziele Panel öffnen → eingeben → Speichern. Oder API / Chat:
```
/objectives.set Dokumentation verbessern;Tests erhöhen
```

### 3. Analyse starten
Nutze:
```
/analyze
```
Antwort enthält strukturierte Vorschläge (id, title, rationale, diff_hint). Wähle eine Idee aus.

### 4. Idee in Pending verwandeln
Über UI (später Button) oder aktuell via:
```
POST /inject-proposal
```
oder Chat-Kommando (wenn vorhanden) – ansonsten nutze das Beispiel aus README weiter unten. Danach erscheint der Vorschlag in Pending Liste (`/pending`).

### 5. Vorschlag anwenden
```
POST /apply/<id>
```
oder zukünftiger UI Button „Apply“. Prüfe diff vorher mit:
```
GET /preview/<id>
```

### 6. Undo bei Bedarf
```
POST /undo
```

### 7. Erneute Analyse (Feedback Loop)
Nach Apply erneut `/analyze` ausführen um neue Optimierungsmöglichkeiten basierend auf geändertem Zustand zu erhalten.

### Häufige Stolpersteine
| Problem | Hinweis |
|---------|---------|
| Keine Vorschläge | Noch keine Ziele gesetzt → `/objectives.set` |
| Diff wirkt leer | diff_hint ist nur ein Hinweis – richtige Diff entsteht erst durch Evolution/Manuell |
| Apply Fehler | Prüfe `preview` und Dateipfade; manche generierten Diffs können ggf. angepasst werden |

### Häufig genutzte Chat-Kommandos
| Kommando | Zweck |
|----------|------|
| /help | Übersicht aller Chat-Befehle |
| /objectives.list | Aktuelle Ziele anzeigen |
| /objectives.set A;B | Ziele setzen / überschreiben |
| /analyze | Repo + Ziele analysieren |
| /kb.list | Knowledge Einträge anzeigen |
| /kb.save | Letzte Assistant Nachricht speichern |
| /world.init 20 12 | 2D Welt initialisieren (Breite=20, Höhe=12) |
| /world.spawn agent alpha | Entity erzeugen |
| /world.tick 5 | Welt 5 Schritte simulieren |
| /world.ents | Entities auflisten |

### UI Schnellbefehle (Inspiration)
Eine neue Seitenleiste bietet Buttons mit vorgefertigten Kommandos & Prompts (z.B. „Analyse“, „Ziele anzeigen“, „Welt tick“). Klick = sofort senden; Shift+Klick = nur ins Eingabefeld übernehmen (zum Anpassen). Die Sammlung wird dynamisch vom Endpoint `/ui/suggestions` geladen (erweiterbar in `src/api/app.py::_build_suggestions`). Fallback: statische Minimalmenge falls Endpoint fehlschlägt.

### Proposal Panel (Neu)
Ein zusätzliches Panel „Proposals“ zeigt Pending Vorschläge strukturiert (ID, Titel, Score). Aktionen:
* Refresh (lädt `/proposals/pending`)
* Diff (lädt `/proposals/preview/<id>`, zeigt Unified Diff inline – gekürzt)
* Apply (POST `/proposals/apply`)
* Undo (POST `/proposals/undo` – letzte angewandte Änderung)

REST Endpoints (UI nutzt sie intern):
```
GET  /proposals/pending
GET  /proposals/preview/{id}
POST /proposals/apply {"id": "p1"} (Header: X-API-Key wenn API_KEY gesetzt)
POST /proposals/undo (Header: X-API-Key)
GET  /analysis/json (strukturierte letzte Analyse)
POST /analysis/inject {id,title,rationale,diff_hint?} (Header: X-API-Key)
```
Damit lässt sich der Evolutionsfluss auch skriptbar in CI integrieren.

### Warum Chat UND REST API?
| Aspekt | Chat | REST API |
|--------|------|----------|
| Schnelligkeit | Ad-hoc | Skripting / Automatisierung |
| Transparenz | Gesprächskontext | Klare JSON Antworten |
| Reproduzierbarkeit | Geringer (freier Text) | Hoch (curl / CI Pipeline) |

Empfehlung: Ideen & Exploration per Chat, verlässliche Workflows (z.B. nightly Analyse) via Skript und API.

---

## Voraussetzungen & Environment
Erforderlich:
* Python 3.11+
* Git installiert
* (Optional) Groq API Key für echte LLM Proposals
 * (Optional) API_KEY für gesicherte Änderungsendpunkte (Apply/Undo/Injection)

Empfohlen: 4+ GB RAM.

`.env` Datei (siehe `.env.example`):
```
GROQ_API_KEY=dein_key_hier    # leer lassen = Dry-Run / Fallback
EVOLUTION_MODEL=gemma2-9b-it  # optional Override
API_KEY=mein_geheimer_key     # schützt /proposals/apply /proposals/undo /chat/to-proposal /analysis/inject
```

Aktiviere Virtual Environment vor allen Befehlen. Unter Windows PowerShell:
```
python -m venv .venv
./.venv/Scripts/Activate.ps1
```

## Architektur (Kurz)
```
User UI (Single Page / FastAPI) ─┬─ /cycle  -> EvolutionAgent (generiert Vorschläge)
								├─ /apply  -> Approval & Score Gate
								├─ /twin   -> TwinCoordinator (sandbox/)
								├─ /snapshot -> SnapshotManager
								├─ /analyze -> Repository Sampling + Ziele
								├─ /chat   -> Chat + Persistenter Verlauf
								└─ /groq-check -> LLM Erreichbarkeit

GroqClient -> LLM Modelle (oder Fake/Fallback) → JSON Proposals
State (.evo_state.json) speichert Cycle, Pending, Objectives
logs/ enthält: chat_history.json, timing Logs, evtl. spätere Metriken
```

Kernideen:
* Deterministischer Diff-Fluss (Preview vor Apply)
* Sandbox ermöglicht risikolose Mehrfachzyklen
* Objectives formen Prompt-Kontext
* Chat als kollaboratives Steuerinstrument

## Twin / Sandbox (Zusammenfassung)
Details weiter unten im vorhandenen Abschnitt „Twin / Klon System (Neu)“. Dieser Abschnitt bleibt Quelle für tiefergehende Nutzung. Hier nur Kurzvorteile:
* Isoliertes Explorieren
* Schnelle Resets & Snapshots
* Selektive Promotion einzelner Dateien

## Ziele / Analyse (Kurzübersicht)
* POST `/objectives` setzt Ziele
* GET `/analyze` liefert strukturierte Suggestions (id, title, rationale, diff_hint)
* POST `/inject-proposal` konvertiert Suggestion → Pending-Eintrag

## Chat (Kurz)
* Persistenz: `logs/chat_history.json` (max 50)
* Kontext: Einbettung der Objectives
* Optional direkte Umwandlung letzter Assistant-Antwort in Proposal (`/chat/to-proposal`)

---

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

## Tests & Qualität (Geplant)
Aktuell keine produktiven Tests eingebracht. Vorgesehene Toolchain:
* pytest – Unit/Integration Tests
* ruff – Linting
* mypy – Typprüfung
* black / isort – Format / Imports

### Aktuelle Minimaltests (Variante go)
Enthaltene Tests:
* `tests/test_world_resources.py` – prüft Wachstum & Caps der Welt-Ressourcen
* `tests/test_analysis_limits.py` – prüft Sampling-Limit (<=100 Dateien) & einfache Analyse-Rückgabe

Ausführen (im aktivierten venv):
```
python -m pytest -q
```
Oder mit detaillierter Ausgabe:
```
python -m pytest -vv
```

Bei Importfehlern sicherstellen, dass Arbeitsverzeichnis Projektwurzel ist und `requirements.txt` installiert wurde:
```
pip install -r requirements.txt
```

Beispiel (zukünftig):
```
pytest -q
ruff check .
mypy src/
```

## Troubleshooting
| Problem | Ursache | Lösung |
|---------|---------|--------|
| Port belegt | Vorheriger Prozess läuft | Anderen Port wählen: `--port 8105` |
| Kein LLM Output / nur Platzhalter | Kein GROQ_API_KEY | `.env` prüfen, `/groq-check` aufrufen |
| Chat zeigt nichts | Browser Cache | Hard Reload (Ctrl+F5) |
| Sandbox hängt | Inkonstenter Zustand | `/twin/reset` oder `.twin/sandbox` löschen |
| Vorschlag Apply Fehler | Diff Konflikt / invalider Patch | Preview prüfen, ggf. Undo und erneut generieren |
| Snapshot Restore ohne Effekt | Falsche ID | `/snapshot/list` prüfen |

## Contribution Guide
Grundprinzipien:
* Kleine, fokussierte Änderungen
* Klarer Titel & rationale im Proposal (Imperativ)
* Konsistente Diff Struktur (Unified)

Branches (Empfehlung):
```
feature/<kurz-beschreibung>
fix/<issue-id-oder-kurz>
docs/<bereich>
```

Neuen Endpoint hinzufügen:
1. Pydantic Model im `app.py` definieren
2. FastAPI Route + Response Model
3. (Optional) UI Hook (Button / Panel)
4. README erweitern falls öffentlich

LLM Prompt-Anpassungen: In GroqClient oder beim Zusammenbau der Nachrichten; Objectives bewusst knapp halten (1 Zeile pro Ziel).

## Glossar
| Begriff | Bedeutung |
|---------|-----------|
| Cycle | Ein Generationslauf neuer Vorschläge (dry-run) |
| Pending | Warteschlange noch nicht angewandter Vorschläge |
| Proposal | Strukturierter Änderungsvorschlag (Diff + Metadaten) |
| Twin / Sandbox | Klon des Repos für isolierte Experimente |
| Promotion | Kopieren veränderter Sandbox-Dateien ins Hauptrepo |
| Snapshot | Gespeicherter Zustand zur Wiederherstellung |
| Objectives | Zielvorgaben zur Steuerung von Analyse & Chat |
| Diff Hint | Grober Hinweistext auf mögliche Änderungen |

## Roadmap (Erweitert)
| Idee | Nutzen | Status |
|------|-------|--------|
| Automatische Analyse nach Apply | Kontinuierliche Ziel-Refresher | Offen |
| Diff-Synthese aus diff_hint | Schnellere vollständige Diffs | Teilweise (Helper vorhanden) |
| Health Gate vor Promotion | Qualitätssicherung | Offen |
| Parallele Sandboxen | Strategievergleich | Offen |
| Persistente Chat-Tags | Bessere Historik-Suche | Offen |
| Token/Latenz Metriken | Transparenz Performance | Offen |
| Echte Streaming Tokens | UX flüssiger | Offen |
| Test-Suite Grundstock | Stabilität | Offen |

---

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

