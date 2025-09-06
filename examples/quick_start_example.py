"""Minimal scripted interaction with the Evolution Sandbox API.

Steps performed:
1. Start (assumes API already running on localhost:8099) or fallback to direct orchestrator usage.
2. Set objectives via HTTP.
3. Trigger analysis and inject first suggestion as proposal.
4. List pending proposals and (optionally) apply the first one.

Run (PowerShell):
  python examples/quick_start_example.py

If the API is NOT running, this script will fallback to direct in-process calls using Orchestrator
(producing proposals without HTTP). This keeps the example self-contained.
"""
from __future__ import annotations
import os, json
from pathlib import Path
import http.client

API_URL = "127.0.0.1"
API_PORT = 8099

OBJECTIVES = ["Dokumentation verbessern", "Tests erhöhen"]


from typing import Any, Dict, List, Optional, Tuple


def api_post(path: str, payload: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    conn = http.client.HTTPConnection(API_URL, API_PORT, timeout=10)
    body = json.dumps(payload or {})
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("API_KEY")
    if api_key:
        headers["X-API-Key"] = api_key
    conn.request("POST", path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", errors="replace")
    try:
        return resp.status, json.loads(data)
    except Exception:
        return resp.status, {"raw": data}


def api_get(path: str) -> Tuple[int, Dict[str, Any]]:
    conn = http.client.HTTPConnection(API_URL, API_PORT, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8", errors="replace")
    try:
        return resp.status, json.loads(data)
    except Exception:
        return resp.status, {"raw": data}


def try_http_flow():
    print("[HTTP] Setting objectives...")
    s, r = api_post("/objectives", {"objectives": OBJECTIVES})
    print(" ->", s, r)

    print("[HTTP] Running analysis...")
    s, r = api_get("/analyze")
    print(" ->", s)
    if s != 200:
        print("[HTTP] Analysis failed, aborting HTTP flow.")
        return False

    # r may be a list of suggestion dicts or wrapper
    raw: Any = r
    suggestions: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        suggestions = [s for s in raw if isinstance(s, dict)]  # type: ignore[arg-type]
    if not suggestions:
        print("[HTTP] No suggestions returned.")
        return True

    first: Dict[str, Any] = suggestions[0]
    inject_payload: Dict[str, Any] = {
        "title": str(first.get("title", "Imported Suggestion")),
        "rationale": str(first.get("rationale", "")),
        "diff": str(first.get("diff_hint", "# placeholder diff\n"))
    }
    print("[HTTP] Injecting first suggestion as proposal...")
    s, r = api_post("/inject-proposal", inject_payload)
    print(" ->", s, r)

    print("[HTTP] Listing pending proposals...")
    s, r = api_get("/pending")
    print(" ->", s)
    if s == 200 and isinstance(r, list) and len(r) > 0:
        # treat r explicitly as list of dict for example purposes
        r_list: List[Dict[str, Any]] = [d for d in r if isinstance(d, dict)]  # type: ignore[list-item]
        first_id: Optional[str] = None
        if r_list:
            _raw_id_any = r_list[0].get("id")
            if isinstance(_raw_id_any, str) and _raw_id_any:
                first_id = _raw_id_any
        if first_id and os.getenv("APPLY_EXAMPLE") == "1":
            print(f"[HTTP] Applying first proposal {first_id}...")
            s_apply, r_apply = api_post(f"/apply/{first_id}")
            print(" ->", s_apply, r_apply)
    return True


def fallback_direct():
    print("[DIRECT] API not reachable – using Orchestrator directly.")
    from src.core.orchestrator import Orchestrator
    repo_root = Path(__file__).resolve().parents[1]
    orch = Orchestrator(repo_root=repo_root)
    orch.state.objectives = OBJECTIVES
    scored = orch.cycle(dry_run=True)
    print(f"Generated {len(scored)} proposals (dry-run). Titles:")
    for p in scored[:5]:
        print(" -", p.title)


if __name__ == "__main__":
    ok = try_http_flow()
    if not ok:
        fallback_direct()
    print("Done.")
