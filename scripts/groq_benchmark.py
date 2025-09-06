from __future__ import annotations
import argparse
import json
import time
from pathlib import Path
from typing import List, Dict, Any
import concurrent.futures

import sys
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.io.groq_client import GroqClient  # type: ignore
from src.core.models import Message  # type: ignore

DEFAULT_MODELS = [
    # Bekannte funktionierende / getestete oder verbreitete Kandidaten
    "gemma2-9b-it",
    "gemma-7b-it",
    "llama3-8b-instruct",
    "llama3-8b-8192",
    "llama3-70b-instruct",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
]

def run_probe(model: str, prompt: str, timeout: float) -> Dict[str, Any]:
    """Execute a single model probe with a hard timeout.

    The Groq client call is wrapped in a thread so we can enforce a timeout
    (simple & portable). If the timeout elapses we mark the probe as failed.
    """
    client = GroqClient()
    client.cfg.model = model  # override per probe
    start = time.time()

    def _invoke() -> str:
        return client.chat_completion([
            Message(role="system", content="Kurz antworten."),
            Message(role="user", content=prompt)
        ])

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_invoke)
            resp = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        elapsed = time.time() - start
        return {
            "model": model,
            "ok": False,
            "error": f"timeout>{timeout}s",
            "latency_sec": round(elapsed, 3),
        }
    except Exception as e:  # pragma: no cover
        elapsed = time.time() - start
        return {
            "model": model,
            "ok": False,
            "error": str(e),
            "latency_sec": round(elapsed, 3),
        }

    elapsed = time.time() - start
    ok = not resp.startswith("[http-error") and not resp.startswith("[network-error")
    return {
        "model": model,
        "ok": ok,
        "response_preview": resp[:160],
        "chars": len(resp),
        "latency_sec": round(elapsed, 3),
    }

def main() -> None:
    p = argparse.ArgumentParser("Groq Modell Benchmark")
    p.add_argument("--candidates", type=str, default=None, help="Komma-separierte Modellliste (überschreibt Default)")
    p.add_argument("--prompt", type=str, default="Sag einen kurzen Testsatz.", help="Prompt für Test")
    p.add_argument("--timeout", type=float, default=30.0, help="Timeout (Sekunden)")
    p.add_argument("--output", type=str, default=None, help="Ergebnis JSON-Datei (optional)")
    args = p.parse_args()

    models: List[str] = DEFAULT_MODELS
    if args.candidates:
        custom = [m.strip() for m in args.candidates.split(',') if m.strip()]
        if custom:
            models = custom

    results: List[Dict[str, Any]] = []
    print("[Benchmark] Teste Modelle:")
    for m in models:
        print(f"  - {m} ...", end="", flush=True)
        r = run_probe(m, args.prompt, args.timeout)
        status = "OK" if r.get("ok") else "FAIL"
        print(f" {status} ({r.get('latency_sec','?')}s)")
        results.append(r)

    # Ranking nach Erfolg, dann nach Latenz, dann nach Zeichenlänge
    ranked = sorted(results, key=lambda x: (not x.get("ok"), x.get("latency_sec", 9999), -x.get("chars", 0)))

    # Aggregate KPIs (avoid 'unused variable' and provide quick glance)
    successes = [r for r in results if r.get("ok")]
    fail = len(results) - len(successes)
    avg_latency = round(sum(r.get("latency_sec", 0) for r in successes)/len(successes), 3) if successes else None
    summary: Dict[str, Any] = {
        "prompt": args.prompt,
        "models_total": len(results),
        "success": len(successes),
        "fail": fail,
        "success_rate": round(len(successes)/len(results), 3) if results else 0.0,
        "avg_latency_success": avg_latency,
        "best_model": ranked[0]["model"] if ranked else None,
        "results": results,
        "ranked": ranked,
    }

    if args.output:
        Path(args.output).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[Benchmark] Ergebnisse gespeichert in {args.output}")
    else:
        print("\nTop 3 (falls vorhanden):")
        for item in ranked[:3]:
            print(f" - {item['model']} :: ok={item['ok']} latency={item.get('latency_sec')}s chars={item.get('chars')}")
        print("\nZusammenfassung:")
        print(json.dumps({k: v for k, v in summary.items() if k not in ("results", "ranked")}, ensure_ascii=False, indent=2))

if __name__ == "__main__":  # pragma: no cover
    main()
