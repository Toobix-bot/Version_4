#!/usr/bin/env python
from __future__ import annotations
"""Ein kleines Skript zur Validierung der Groq API Umgebung.

Exit Codes:
 0 = OK
 1 = Kein Key gesetzt
 2 = Verdächtiger / ungültiger Key (Pattern-Prüfung fehlgeschlagen)

Verwendung (PowerShell):
  python scripts/check_env.py
"""
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv  # type: ignore
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore


def load_env() -> None:
    # Falls .env existiert, laden (optional)
    if load_dotenv:
        env_file = Path(__file__).resolve().parent.parent / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file)  # type: ignore


def validate_api_key(value: str | None) -> int:
    if not value:
        print("[ENV] API_KEY fehlt.")
        return 1
    # Simple heuristik: Groq Keys beginnen (Stand heute) oft mit 'gsk_'
    if not value.startswith("gsk_"):
        print("[ENV] API_KEY gesetzt, aber Pattern stimmt nicht ('gsk_' erwartet) -> weiterprüfen.")
        return 2
    if len(value) < 40:
        print("[ENV] API_KEY zu kurz (<40).")
        return 2
    print("[ENV] API_KEY OK.")
    return 0


def main() -> int:
    load_env()
    key = os.getenv("API_KEY")
    result = validate_api_key(key)
    if result == 0:
        print("Environment: OK")
    else:
        print("Environment: PROBLEM (Code", result, ")")
    return result


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
