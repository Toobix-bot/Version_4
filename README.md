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
