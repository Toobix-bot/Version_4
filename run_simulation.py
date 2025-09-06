from __future__ import annotations
import argparse
from pathlib import Path
from src.core.orchestrator import Orchestrator


def _print_simple_intro():
    print("\n=== Evolution Mini-Tool ===")
    print("1. Erst Vorschläge sammeln (--cycles N)")
    print("2. Vorschläge ansehen (stehen als 'Pending')")
    print("3. Einen anwenden: --apply ID (entfernt aus Pending und schreibt Datei)")
    print("Hinweis: --dry-run verhindert echte Änderungen.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run evolution simulation loop")
    parser.add_argument("--cycles", type=int, default=1, help="Anzahl der Durchläufe (Vorschlagsrunden)")
    parser.add_argument("--dry-run", action="store_true", help="Nichts wirklich schreiben (nur anzeigen)")
    parser.add_argument("--apply", type=str, default=None, help="Proposal ID anwenden (nach Zyklen)")
    parser.add_argument("--auto-approve-all", action="store_true", help="Alle Pending Vorschläge automatisch anwenden (nicht im dry-run)")
    parser.add_argument("--undo-last", action="store_true", help="Letzten akzeptierten Vorschlag rückgängig machen (Metadaten)")
    parser.add_argument("--json", action="store_true", help="Ergebnisse als JSON ausgeben")
    parser.add_argument("--reset-state", action="store_true", help="Löscht bestehenden Evolutionszustand (.evo_state.json) vor Start")
    parser.add_argument("--groq-model", type=str, default=None, help="Überschreibt das Modell für diesen Lauf")
    parser.add_argument("--groq-list", action="store_true", help="Listet verfügbare Modelle (nur Groq) und beendet dann")
    parser.add_argument("--groq-raw-call", type=str, default=None, help="Direkter Prompt an Groq (umgeht EvolutionAgent). Gibt Roh-Content aus und beendet")
    parser.add_argument("--groq-scan", action="store_true", help="Testet mehrere bekannte Modellnamen nacheinander")
    parser.add_argument("--groq-scan-list", type=str, default=None, help="Komma-separierte Modellliste zum Test (überschreibt Default)")
    args = parser.parse_args()

    repo_root = Path(__file__).parent
    state_file = repo_root / ".evo_state.json"
    if args.reset_state and state_file.exists():
        try:
            state_file.unlink()
            if not args.json:
                print("[RESET] Zustand gelöscht.")
        except Exception as e:  # pragma: no cover
            if not args.json:
                print(f"[WARN] Konnte Zustand nicht löschen: {e}")

    orch = Orchestrator(repo_root=repo_root)

    # Groq model override (runtime only)
    if args.groq_model:
        try:
            from src.io.config import load_config
            cfg = load_config()
            cfg.model = args.groq_model  # local override object (agents will read on new client init)
            # Force re-init evolution agent with updated model
            from src.core.agents import EvolutionAgent
            orch.evolution = EvolutionAgent(repo_root=repo_root)
            if not args.json:
                print(f"[GROQ] Modell überschrieben: {args.groq_model}")
        except Exception as e:  # pragma: no cover
            if not args.json:
                print(f"[WARN] Konnte Modell nicht setzen: {e}")

    if args.groq_list:
        try:
            from src.io.groq_client import GroqClient
            raw = GroqClient().list_models()
            print(raw)
        except Exception as e:  # pragma: no cover
            print(f"[FEHLER] Modelle konnten nicht geladen werden: {e}")
        return
    if args.groq_raw_call:
        try:
            from src.io.groq_client import GroqClient
            from src.core.models import Message
            client = GroqClient()
            prompt = args.groq_raw_call
            resp = client.chat_completion([
                Message(role="system", content="Kurzer Test"),
                Message(role="user", content=prompt)
            ])
            print("RAW:")
            print(resp)
        except Exception as e:  # pragma: no cover
            print(f"[FEHLER] Raw Call fehlgeschlagen: {e}")
        return
    if args.groq_scan:
        try:
            from src.io.groq_client import GroqClient
            from src.core.models import Message
            client = GroqClient()
            base_list = [
                "llama3-8b-instruct",
                "llama3-8b-8192",
                "llama3-70b-instruct",
                "llama3-70b-8192",
                "mixtral-8x7b-32768",
                "gemma-7b-it",
                "gemma2-9b-it",
            ]
            if args.groq_scan_list:
                user_list = [m.strip() for m in args.groq_scan_list.split(',') if m.strip()]
                if user_list:
                    base_list = user_list
            print("[GROQ-SCAN] Starte Test folgender Modelle:")
            for m in base_list:
                client.cfg.model = m
                res = client.chat_completion([Message(role='user', content='ping')])
                status = res[:120].replace('\n', ' ')
                print(f" - {m}: {status}")
        except Exception as e:  # pragma: no cover
            print(f"[FEHLER] Scan fehlgeschlagen: {e}")
        return
    _print_simple_intro()
    import json as _json
    all_scored = []
    for _ in range(args.cycles):
        scored = orch.cycle(dry_run=args.dry_run)
        all_scored.extend(scored)
        if not args.json:
            print("[Durchlauf] Neue Vorschläge:")
            for p in scored:
                sc = f"{p.score.composite:.2f}" if p.score else "n/a"
                print(f" - {p.id} :: {p.title} (Score {sc})")

    pending = orch.list_pending()
    if args.json:
        out = {
            "cycle": orch.state.cycle,
            "new_proposals": [
                {"id": p.id, "title": p.title, "score": p.score.composite if p.score else None} for p in all_scored
            ],
            "pending": [
                {"id": p.id, "title": p.title, "score": p.score.composite if p.score else None} for p in pending
            ]
        }
        print(_json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if pending:
            print("\nNoch offen (Pending):")
            for p in pending:
                sc = f"{p.score.composite:.2f}" if p.score else "?"
                print(f" * {p.id}: {p.title} | Score {sc}")
        else:
            print("Keine Pending Vorschläge.")

    # Auto-Approve
    if args.auto_approve_all and not args.dry_run:
        for p in list(pending):  # copy
            applied_path = orch.apply_after_approval(p.id, dry_run=args.dry_run)
            print(f"[AUTO] Angewendet: {p.id} -> {applied_path}")

    # Single apply
    if args.apply and not args.dry_run:
        try:
            applied_path = orch.apply_after_approval(args.apply, dry_run=args.dry_run)
            print(f"[OK] Vorschlag '{args.apply}' angewendet: {applied_path}")
        except Exception as e:  # pragma: no cover
            print(f"[FEHLER] Konnte nicht anwenden: {e}")
    elif args.apply and args.dry_run:
        print("[INFO] --apply ignoriert wegen --dry-run.")

    if args.undo_last:
        undone = orch.undo_last()
        if undone:
            print(f"[UNDO] Letzter Eintrag entfernt (ID={undone}).")
        else:
            print("[UNDO] Nichts zu entfernen.")

if __name__ == "__main__":  # pragma: no cover
    main()
