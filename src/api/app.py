from __future__ import annotations
from fastapi import FastAPI
from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pathlib import Path
from typing import List, Any, Dict, TypedDict, cast
from ..core.orchestrator import Orchestrator
from ..io.groq_client import GroqClient
from ..core.twin import TwinCoordinator, SnapshotManager
from ..core.analysis import analyze_repository
from ..core import indexer as code_index
import time, asyncio, threading
try:
    from anyio import to_thread
except Exception:  # fallback if anyio import pattern changes
    to_thread = None  # type: ignore

app = FastAPI(title="Evolution Sandbox API", version="0.1.1")
# CORS for local dev / browser testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
repo_root = Path(__file__).resolve().parent.parent.parent
orch = Orchestrator(repo_root=repo_root)
gclient = GroqClient()
twin = TwinCoordinator(repo_root=repo_root)
snaps = SnapshotManager(repo_root=repo_root)
import os
API_KEY_REQUIRED = os.environ.get('API_KEY') or ''
RATE_LIMIT_PER_MIN = int(os.environ.get('RATE_LIMIT_PER_MIN', '120'))  # global simple limit
# Role mapping env: API_TOKENS="k1:read,k2:write,k3:admin"
_RAW_TOKEN_MAP = os.environ.get('API_TOKENS','').strip()
TOKEN_ROLES: Dict[str,str] = {}
if _RAW_TOKEN_MAP:
    for part in _RAW_TOKEN_MAP.split(','):
        if ':' in part:
            token, role = part.split(':',1)
            token = token.strip(); role = role.strip().lower()
            if token:
                TOKEN_ROLES[token] = role or 'read'

def _resolve_role(token: str | None) -> str:
    if not TOKEN_ROLES:
        if API_KEY_REQUIRED and token == API_KEY_REQUIRED:
            return 'admin'
        return 'public'
    if token and token in TOKEN_ROLES:
        return TOKEN_ROLES[token]
    return 'public'

# --- Simple in-memory rate limiter (global & per-IP) --- #
_rl_lock = threading.Lock()
_rl_window_start = time.time()
_rl_global_count = 0
_rl_ip_counts: Dict[str,int] = {}

def _rate_limit_allow(ip: str) -> bool:
    global _rl_window_start, _rl_global_count
    now = time.time()
    with _rl_lock:
        if now - _rl_window_start >= 60.0:
            _rl_window_start = now
            _rl_global_count = 0
            _rl_ip_counts.clear()
        _rl_global_count += 1
        _rl_ip_counts[ip] = _rl_ip_counts.get(ip,0)+1
        if _rl_global_count > RATE_LIMIT_PER_MIN:
            return False
        if _rl_ip_counts[ip] > max(10, RATE_LIMIT_PER_MIN//4):
            return False
        return True

from typing import Callable, Awaitable
from starlette.responses import Response

@app.middleware('http')
async def rate_limit_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]):  # pragma: no cover (best effort)
    ip = request.client.host if request.client else 'unknown'
    # read current value dynamically (tests may patch RATE_LIMIT_PER_MIN)
    limit = globals().get('RATE_LIMIT_PER_MIN', 0)
    import os as _os
    if _os.getenv('DISABLE_RATE_LIMIT') == '1':
        limit = 0
    if isinstance(limit, int) and limit > 0:
        if not _rate_limit_allow(ip):
            return JSONResponse(status_code=429, content={'error':'rate_limited','detail':'Too many requests','limit':limit})
    try:
        resp = await call_next(request)
        return resp
    except Exception as e:
        # fallback catch (should be handled by exception handler too)
        return JSONResponse(status_code=500, content={'error':'internal_error','detail':str(e)[:300]})

# --- Structured exception handler --- #
class ErrorCodes:
    INVALID_KEY = 'ERR_INVALID_KEY'
    FORBIDDEN = 'ERR_FORBIDDEN'
    POLICY = 'ERR_POLICY_VIOLATION'

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):  # pragma: no cover
    payload = {'error':'exception','detail':str(exc)[:300],'path':str(request.url.path)}
    if 'policy' in str(exc).lower():
        payload['code'] = ErrorCodes.POLICY
    return JSONResponse(status_code=500, content=payload)

def api_key_guard(x_api_key: str | None = Header(default=None), required: str = 'write'):
    # required: read|write|admin
    role = _resolve_role(x_api_key)
    hierarchy = {'public':0,'read':1,'write':2,'admin':3}
    need = hierarchy.get(required,1)
    have = hierarchy.get(role,0)
    if API_KEY_REQUIRED and not TOKEN_ROLES:  # legacy single key mode
        if x_api_key != API_KEY_REQUIRED:
            raise HTTPException(status_code=401, detail={"code":ErrorCodes.INVALID_KEY,"msg":"invalid or missing API key"})
        return True
    if have < need:
        raise HTTPException(status_code=403, detail={"code":ErrorCodes.FORBIDDEN,"msg":"insufficient role","need":required,"have":role})
    return True
class PersonaDict(TypedDict):
    id: str
    label: str
    style: str

class MultiOutputDict(TypedDict):
    id: str
    label: str
    text: str

    timestamp: float
    reflections: Dict[str, str]
    decision: str
    next_hint: str
    evaluation: Dict[str, Any]
    conflict: bool

# ---- Modular command system (repaired) ---- #
COMMAND_CATEGORIES: Dict[str, List[tuple[str,str]]] = {
    'System': [
        ('meta','Status'),('cycle','Dry Run Zyklus'),('pending','Liste Pending'),('apply','Apply Proposal'),
        ('diff','Diff Vorschau'),('undo','Undo letzter Apply'),('twin.changed','Sandbox Änderungen'),('snapshot.list','Snapshots')],
    'Ziele': [('objectives.list','Ziele zeigen'),('objectives.set','Ziele setzen')],
    'Knowledge': [('kb.save','Speichern'),('kb.list','Auflisten'),('kb.search','Suchen'),('kb.get','Zeigen'),('kb.inject','In Chat injizieren')],
    'Analyse': [('analyze','Repo Analyse'),('analysis.last','Letzte Analyse')],
    'World': [('world.init','Welt init'),('world.spawn','Spawn'),('world.tick','Ticks'),('world.ents','Entities'),('world.ctrl','Control'),('world.move','Bewegen'),('world.info','Info'),('world.state','JSON State')],
    'Improve': [('improve.scan','Heuristik Scan')]
}
from ..sim import world as sim_world

# ---- Minimal state & persistence (restored) ---- #
repo_logs = repo_root / 'logs'
repo_logs.mkdir(exist_ok=True)

_chat_history: List[Dict[str, Any]] = []
MAX_CHAT_MESSAGES = 60

_knowledge_index: List[Dict[str, Any]] = []
_reflections: List[Dict[str, Any]] = []
_long_memory: List[Dict[str, Any]] = []
_last_multi_outputs: List[Dict[str,str]] = []
_notebooks: List[Dict[str, Any]] = []
_energy: Dict[str, float] = { 'focus': 0.5, 'harmony': 0.5, 'fatigue': 0.2 }
_self_dialog: List[Dict[str, Any]] = []
_auto_coach_enabled = False
_last_analysis: List[Dict[str, Any]] = []
_last_improve: List[Dict[str, Any]] = []

PERSONAS: List[Dict[str,str]] = [
    {'id':'denker','label':'Denker','style':'analytisch|präzise'},
    {'id':'kritiker','label':'Kritiker','style':'risiko|bohrend'},
    {'id':'wahrheit','label':'Wahrheit','style':'evidenz|klar'},
    {'id':'vision','label':'Visionär','style':'zukunft|möglichkeiten'}
]
ACTIVE_PERSONA_IDS: List[str] = []
PERSONA_GRAPH: Dict[str, Dict[str,float]] = {}
_user_model: Dict[str, Any] = {'traits':[], 'values':[], 'interests':[], 'tone':'neutral', 'updated': time.time()}

def _save_json(path: Path, data: Any):
    try:
        path.parent.mkdir(exist_ok=True)
        import json as _j
        path.write_text(_j.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass

def _save_user_model():
    _save_json(repo_logs / 'user_model.json', _user_model)

def _save_personas():
    _save_json(repo_logs / 'personas.json', PERSONAS)

def _load_personas():
    path = repo_logs / 'personas.json'
    if not path.exists():
        return
    try:
        import json as _j
        raw: Any = _j.loads(path.read_text(encoding='utf-8'))
        if isinstance(raw, list):
            cleaned: List[Dict[str, str]] = []
            for p in raw:  # type: ignore[assignment]
                if isinstance(p, dict) and 'id' in p:
                    p = p  # type: ignore[assignment]
                    pid: str = str(p.get('id',''))  # type: ignore[call-arg]
                    label: str = str(p.get('label', pid))  # type: ignore[call-arg]
                    style: str = str(p.get('style',''))  # type: ignore[call-arg]
                    cleaned.append({'id': pid, 'label': label, 'style': style})
            PERSONAS.clear(); PERSONAS.extend(cleaned)
    except Exception:
        pass

def _save_reflections(): _save_json(repo_logs / 'reflections.json', _reflections)
def _save_knowledge(): _save_json(repo_logs / 'knowledge.json', _knowledge_index)
def _save_long_memory(): _save_json(repo_logs / 'long_memory.json', _long_memory)
def _save_notebooks(): _save_json(repo_logs / 'notebooks.json', _notebooks)
def _save_energy(): _save_json(repo_logs / 'energy.json', _energy)
def _save_chat_history(): _save_json(repo_logs / 'chat_history.json', _chat_history[-MAX_CHAT_MESSAGES:])

# ---- Loaders (basic persistence restore) ---- #
def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        import json as _j
        return _j.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None

def _load_chat_history():
    raw = _load_json(repo_logs / 'chat_history.json')
    if isinstance(raw, list):
        for _m in cast(List[Any], raw[-MAX_CHAT_MESSAGES:]):
            if isinstance(_m, dict) and 'role' in _m and 'content' in _m:
                m: Dict[str, Any] = cast(Dict[str, Any], _m)
                _chat_history.append({'role': str(m.get('role','user')), 'content': str(m.get('content','')), 'ts': float(m.get('ts', time.time()))})

def _load_knowledge():
    raw = _load_json(repo_logs / 'knowledge.json')
    if isinstance(raw, list):
        for _e in cast(List[Any], raw):
            if isinstance(_e, dict) and 'id' in _e:
                e: Dict[str, Any] = cast(Dict[str, Any], _e)
                _knowledge_index.append(e)

def _load_reflections():
    raw = _load_json(repo_logs / 'reflections.json')
    if isinstance(raw, list):
        for _r in cast(List[Any], raw):
            if isinstance(_r, dict):
                r: Dict[str, Any] = cast(Dict[str, Any], _r)
                _reflections.append(r)

def _load_long_memory():
    raw = _load_json(repo_logs / 'long_memory.json')
    if isinstance(raw, list):
        for _r in cast(List[Any], raw):
            if isinstance(_r, dict):
                r: Dict[str, Any] = cast(Dict[str, Any], _r)
                _long_memory.append(r)

def _load_notebooks():
    raw = _load_json(repo_logs / 'notebooks.json')
    if isinstance(raw, list):
        for _n in cast(List[Any], raw):
            if isinstance(_n, dict):
                n: Dict[str, Any] = cast(Dict[str, Any], _n)
                _notebooks.append(n)

def _load_energy():
    raw = _load_json(repo_logs / 'energy.json')
    if isinstance(raw, dict):
        for k in ('focus','harmony','fatigue'):
            if k in raw:
                try: _energy[k] = float(raw[k])  # type: ignore
                except Exception: pass

def _energy_tick():
    # simple drift model
    _energy['focus'] = max(0.0, min(1.0, _energy['focus'] + 0.01 - 0.02*_energy['fatigue']))
    _energy['fatigue'] = max(0.0, min(1.0, _energy['fatigue'] + 0.03))
    if _energy['fatigue'] > 0.7:
        _energy['harmony'] = max(0.0, _energy['harmony'] - 0.01)

async def _self_dialog_tick():
    # trivial internal reflection line
    line: Dict[str, Any] = {'role':'self','content':'tick: focus={:.2f}'.format(_energy['focus']), 'ts': time.time()}
    _self_dialog.append(line)
    if len(_self_dialog) > 120:
        del _self_dialog[0:len(_self_dialog)-120]

# load persisted personas if present
_load_personas(); _load_chat_history(); _load_knowledge(); _load_reflections(); _load_long_memory(); _load_notebooks(); _load_energy()
# configure world persistence
try:
    sim_world.configure_persistence(repo_logs)
except Exception:
    pass
# keep references so analyzer treats helpers as used (lightweight registry)
_KEEP_FUNCS = (_save_user_model, _save_personas, _save_reflections, _save_notebooks)

# ---- Chat models ---- #
from pydantic import Field
class ChatMessage(BaseModel):
    role: str
    content: str
    ts: float

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)

class ChatReply(BaseModel):
    reply: str
    history: List[ChatMessage]

class ChatHistoryResp(BaseModel):
    history: List[ChatMessage]

def _coerce_ts(val: Any) -> float:
    if isinstance(val,(int,float)): return float(val)
    if isinstance(val,str):
        try: return float(val.strip())
        except Exception: return 0.0
    return 0.0

def _history_as_models() -> List[ChatMessage]:
    return [ChatMessage(role=str(h.get('role','user')), content=str(h.get('content','')), ts=_coerce_ts(h.get('ts'))) for h in _chat_history]

def _build_chat_messages(user_text: str):
    from ..core.models import Message
    objectives_txt = '\n'.join(f"- {o}" for o in orch.state.objectives[:8]) or '(keine)'
    sys = ("You are a concise assistant improving this repo. Ziele:\n" + objectives_txt + "\nAntworte knapp.")
    msgs = [Message(role='system', content=sys)]
    for h in _chat_history[-10:]:
        msgs.append(Message(role=str(h.get('role','user')), content=str(h.get('content',''))))
    msgs.append(Message(role='user', content=user_text))
    return msgs

COMMAND_LOOKUP: Dict[str, str] = {c: desc for _, entries in COMMAND_CATEGORIES.items() for c, desc in entries}

def _help_text(cmd: str | None = None) -> str:
    if not cmd:
        lines = ["Befehle (Kategorien):"]
        for cat, entries in COMMAND_CATEGORIES.items():
            cmds = ' '.join('/'+c for c,_ in entries)
            lines.append(f"[{cat}] {cmds}")
        lines.append("/help <cmd> für Detail")
        return '\n'.join(lines)
    c = cmd.lower().strip()
    if c.startswith('/'):
        c = c[1:]
    desc = COMMAND_LOOKUP.get(c)
    if not desc:
        return f"Keine Detailhilfe für {c}."
    # minimal extended hints (can expand later)
    examples = {
        'objectives.set': "/objectives.set Alpha; Beta; Gamma",
        'kb.search': "/kb.search token",
        'apply': "/apply <id-fragment>",
        'diff': "/diff <id>",
        'personas.set': "/personas.set denker,kritiker",
        'analyze': "/analyze (scan repo)",
        'memory.compress': "/memory.compress (>=40 msgs)"
    }
    hint = examples.get(c, f"/{c}")
    return f"/{c}: {desc}\nBeispiel: {hint}"

# --- HTTP Help & Version History --- #
from fastapi import Query
import json as _json

@app.get('/help')
def http_help(cmd: str | None = None):
    return {'help': _help_text(cmd)}

from typing import List as _List, Dict as _Dict, Any as _Any

@app.get('/versions')
def version_history(limit: int = Query(50, ge=1, le=500)) -> _List[_Dict[str, _Any]]:
    path = repo_logs / 'version_history.jsonl'
    if not path.exists():
        return []
    lines = path.read_text(encoding='utf-8').strip().splitlines()[-limit:]
    out: _List[_Dict[str, _Any]] = []
    for ln in lines:
        try:
            out.append(_json.loads(ln))
        except Exception:
            pass
    return list(reversed(out))

def _legacy_logic(cmd: str, arg: str) -> str | None:  # trimmed set
    try:
        if cmd == 'meta':
            return (f"Cycle={orch.state.cycle} Pending={len(orch.list_pending())} "
                    f"TwinChanged={len(twin.diff_changed_files())} Snapshots={len(snaps.list())} Objectives={len(orch.state.objectives)}")
        if cmd == 'cycle':
            scored = orch.cycle(dry_run=True)
            return f"Cycle {orch.state.cycle} erzeugt {len(scored)} Vorschläge." + (" IDs: "+', '.join(p.id for p in scored) if scored else '')
        if cmd == 'pending':
            pend = orch.list_pending()
            if not pend: return 'Keine Pending Vorschläge.'
            return 'Pending: ' + ', '.join(f"{p.id}:{p.title[:18]}" for p in pend)
        if cmd == 'apply':
            pend = orch.list_pending()
            fragment = arg.strip()
            target = None
            if not fragment:
                if len(pend)==1: target = pend[0].id
                else: return 'Nutze: /apply <id|teil>'
            else:
                m = [p for p in pend if p.id.startswith(fragment) or fragment in p.id]
                if not m: return 'Keine Übereinstimmung'
                if len(m)>1: return 'Mehrdeutig: ' + ', '.join(p.id for p in m[:6])
                target = m[0].id
            try:
                orch.apply_after_approval(target, dry_run=False)
                return f'Angewendet: {target}'
            except Exception as e:
                return f'Fehler: {e}'[:180]
        if cmd == 'diff':
            pid = arg.strip()
            if not pid: return 'Nutze: /diff <id>'
            try:
                d = orch.preview(pid)
                return d[:1200]
            except Exception as e:
                return f'Diff Fehler: {e}'[:180]
        if cmd == 'undo':
            res = orch.undo_last(); return f'Undo: {res}' if res else 'Nichts rückgängig.'
        if cmd == 'twin.changed':
            ch = twin.diff_changed_files(); return 'Sandbox Änderungen: ' + (', '.join(ch) if ch else 'Keine')
        if cmd == 'snapshot.list':
            lst = snaps.list(); return 'Snapshots: ' + (', '.join(f"{s.id}:{s.label}" for s in lst) if lst else 'Keine')
        if cmd == 'objectives.list':
            return 'Objectives:\n' + ('\n'.join('- '+o for o in orch.state.objectives) or '(leer)')
        if cmd == 'objectives.set':
            raw = arg.replace('\n',' ').strip();
            if not raw: return 'Nutze: /objectives.set a; b; c'
            parts = [p.strip() for p in raw.replace(';','\n').split('\n') if p.strip()]
            orch.state.objectives = parts[:12]; return f'Gesetzt ({len(parts[:12])})'
        if cmd == 'personas.list':
            active = ACTIVE_PERSONA_IDS if ACTIVE_PERSONA_IDS else '[auto erste]'
            return 'Personas: ' + ', '.join(p['id'] for p in PERSONAS) + f'\nAktiv: {active}'
        if cmd == 'personas.set':
            ids = [i.strip() for i in arg.split(',') if i.strip()]
            valid = {p['id'] for p in PERSONAS}; bad=[i for i in ids if i not in valid]
            if bad: return 'Unbekannt: ' + ', '.join(bad)
            ACTIVE_PERSONA_IDS.clear(); ACTIVE_PERSONA_IDS.extend(ids[:10]); return 'Aktive: ' + ', '.join(ACTIVE_PERSONA_IDS)
        if cmd == 'energy.show':
            return f"Energy focus={_energy['focus']:.2f} harmony={_energy['harmony']:.2f} fatigue={_energy['fatigue']:.2f}"
        if cmd == 'energy.tick':
            _energy_tick(); _save_energy(); return f"Energy (tick) focus={_energy['focus']:.2f} fatigue={_energy['fatigue']:.2f}"
        if cmd == 'self.tick':
            asyncio.create_task(_self_dialog_tick()); return 'Self tick gestartet'
        if cmd == 'self.show':
            return ' | '.join(f"{(d.get('content',''))[:40]}" for d in _self_dialog[-6:]) or '(leer)'
        if cmd == 'memory.compress':
            if len(_chat_history) < 40:
                return 'Zu wenig Verlauf für Kompression.'
            old = _chat_history[:-30]
            snapshot = '\n'.join(f"{m.get('role')}:{m.get('content','')[:160]}" for m in old[-120:])
            from ..core.models import Message
            try:
                if to_thread:
                    summ = to_thread.run_sync(lambda: gclient.chat_completion([
                        Message(role='system', content='Fasse Chatkernpunkte in 5-8 Bullet-Punkten zusammen.'),
                        Message(role='user', content=snapshot[:6000])
                    ]))  # type: ignore
                else:
                    summ = gclient.chat_completion([
                        Message(role='system', content='Fasse Chatkernpunkte in 5-8 Bullet-Punkten zusammen.'),
                        Message(role='user', content=snapshot[:6000])
                    ])
            except Exception as e:
                summ = f"(Fehler {e})"
            summary = summ.strip()[:600] if isinstance(summ,str) else str(summ)[:600]
            _long_memory.append({'timestamp': time.time(), 'summary': summary})
            _save_long_memory()
            return 'Memory komprimiert.'
        if cmd == 'kb.save':
            title = arg.strip() or f"Notiz {time.strftime('%H:%M:%S')}"
            assistants = [m for m in _chat_history if m.get('role')=='assistant']
            if not assistants:
                return 'Keine assistant Nachricht zum Speichern.'
            content = assistants[-1].get('content','')
            kid = f"kb{int(time.time()*1000)}"
            _knowledge_index.append({'id':kid,'title':title[:160],'content':content[:15000],'tags':[],'created':time.time(),'source':'chat'})
            _save_knowledge()
            return f"KB gespeichert: {kid}"
        if cmd == 'kb.list':
            if not _knowledge_index:
                return 'KB leer.'
            latest = sorted(_knowledge_index, key=lambda x: x.get('created',0), reverse=True)[:8]
            return 'KB: ' + '; '.join(f"{e.get('id','')}:{e.get('title','')[:18]}" for e in latest)
        if cmd == 'kb.search':
            term = arg.strip()
            if not term: return 'Nutze: /kb.search <wort>'
            term_l = term.lower(); hits: List[Dict[str, Any]] = []
            for e in _knowledge_index:
                hay = (e.get('title','') + ' ' + e.get('content','')).lower()
                if term_l in hay:
                    hits.append(e)
                if len(hits) >= 8: break
            if not hits: return f"Keine Treffer für '{term}'."
            return 'Treffer: ' + '; '.join(f"{h.get('id','')}:{h.get('title','')[:18]}" for h in hits)
        if cmd == 'kb.get':
            kid = arg.strip()
            for e in _knowledge_index:
                if e.get('id') == kid:
                    return f"{kid} {e.get('title','')}\n" + e.get('content','')[:800]
            return 'Nicht gefunden'
        if cmd == 'kb.inject':
            kid = arg.strip()
            for e in _knowledge_index:
                if e.get('id') == kid:
                    injected = f"[KB:{kid}]\n" + e.get('content','')[:1200]
                    _chat_history.append({'role':'assistant','content':injected,'ts':time.time()})
                    _save_chat_history(); return f"Injiziert: {kid}"
            return 'Nicht gefunden'
        if cmd == 'coach.on':
            global _auto_coach_enabled; _auto_coach_enabled = True; return 'Auto-Coach an'
        if cmd == 'coach.off':
            _auto_coach_enabled = False; return 'Auto-Coach aus'
        if cmd == 'analyze':
            start = time.time()
            try:
                # result: List[SuggestionDict]; treat as list[dict[str,Any]] for normalization
                suggestions: List[Any] = analyze_repository(repo_root, orch.state.objectives, gclient)  # type: ignore
            except Exception:
                suggestions = []
            global _last_analysis
            norm: List[Dict[str, Any]] = []
            for s in suggestions:
                try:
                    if isinstance(s, dict):  # type: ignore[redundant-expr]
                        sid = str(s.get('id',''))
                        title = str(s.get('title',''))[:160]
                        rationale = str(s.get('rationale',''))[:400]
                        diff_hint = s.get('diff_hint')
                    else:
                        sid = str(getattr(s,'id',''))
                        title = str(getattr(s,'title',''))[:160]
                        rationale = str(getattr(s,'rationale',''))[:400]
                        diff_hint = getattr(s,'diff_hint', None)
                    norm.append({'id': sid, 'title': title, 'rationale': rationale, 'diff_hint': diff_hint})
                except Exception:
                    continue
            _last_analysis = norm
            took = (time.time()-start)*1000
            try:
                from ..core import metrics as _metrics
                _metrics.record_analysis(took)
            except Exception:
                pass
            if not norm:
                return f"Analyse leer ({took:.0f}ms)."
            return f"Analyse {len(norm)} Vorschläge ({took:.0f}ms): " + ', '.join(f"{d['id']}:{d['title'][:18]}" for d in norm[:6])
        if cmd == 'analysis.last':
            if not _last_analysis: return 'Noch keine Analyse.'
            return 'Letzte Analyse: ' + ', '.join(f"{d['id']}:{d['title'][:18]}" for d in _last_analysis[:8])
        if cmd == 'improve.scan':
            # Simple local heuristic scan (no LLM) over repo structure
            suggestions: List[Dict[str, Any]] = []
            try:
                py_files = list(repo_root.glob('src/**/*.py'))[:200]
                large = [p for p in py_files if p.stat().st_size > 40_000]
                if large:
                    suggestions.append({'id':'size-trim','title':'Große Dateien reduzieren','rationale': f"{len(large)} Dateien >40KB (z.B. {large[0].name})","diff_hint":"Teile große Module in kleinere logisch kohärente Einheiten."})
                if 'Improve onboarding documentation.' not in orch.state.objectives:
                    suggestions.append({'id':'doc-objective','title':'Objective Onboarding prüfen','rationale':'Onboarding Objective fehlt oder anders benannt.','diff_hint':'README Abschnitt Onboarding hervorheben/ergänzen.'})
                # simple count of pending vs accepted
                if len(orch.list_pending())==0:
                    suggestions.append({'id':'no-pending','title':'Neue Zyklen anstoßen','rationale':'Keine Pending Vorschläge vorhanden.','diff_hint':'Führe /cycle oder /analyze aus.'})
            except Exception:
                pass
            global _last_improve
            _last_improve = suggestions
            return ('Improve Scan ' + (', '.join(s['id'] for s in suggestions) if suggestions else 'leer'))
        # --- World commands ---
        if cmd == 'world.init':
            parts = [p for p in arg.split() if p.strip()]
            if not parts:
                return sim_world.init_world(40,24)
            if len(parts)!=2: return 'Nutze: /world.init <w> <h>'
            try:
                w = int(parts[0]); h = int(parts[1])
            except Exception:
                return 'Ungültige Zahlen.'
            return sim_world.init_world(w,h)
        if cmd == 'world.spawn':
            kind = (arg.split()[0] if arg.strip() else 'agent')
            return sim_world.spawn(kind)
        if cmd == 'world.tick':
            n = 1
            if arg.strip():
                try: n = int(arg.strip())
                except Exception: pass
            return sim_world.tick(n)
        if cmd == 'world.ents':
            return sim_world.entities_summary()
        if cmd == 'world.ctrl':
            eid = arg.strip();
            if not eid: return 'Nutze: /world.ctrl <id>'
            return sim_world.control(eid)
        if cmd == 'world.move':
            parts = [p for p in arg.split() if p.strip()]
            if len(parts)!=2: return 'Nutze: /world.move <dx> <dy>'
            try:
                dx = int(parts[0]); dy = int(parts[1])
            except Exception:
                return 'Ungültig.'
            return sim_world.move(dx,dy)
        if cmd == 'world.info':
            return sim_world.world_info()
        if cmd == 'world.state':
            raw_val = sim_world.STATE.get('entities', [])
            ents_list: List[Dict[str, Any]] = []
            if isinstance(raw_val, list):
                for item_any in raw_val:  # type: ignore[assignment]
                    # Explicitly narrow each element
                    if isinstance(item_any, dict):
                        ent = cast(Dict[str, Any], item_any)
                        ents_list.append(ent)
            st: Dict[str, Any] = {
                'w': int(sim_world.STATE.get('w', 0) or 0),
                'h': int(sim_world.STATE.get('h', 0) or 0),
                'ticks': int(sim_world.STATE.get('ticks', 0) or 0),
                'controlled': sim_world.STATE.get('controlled'),
                'entities': ents_list[:50],
                'entities_total': len(ents_list),
            }
            import json as _json
            return _json.dumps(st, ensure_ascii=False)
    except Exception as e:  # pragma: no cover
        return f'Command Fehler: {e}'[:200]
    return None

ZERO_WIDTH = ('\u200b','\u200c','\u200d','\ufeff')

def _normalize_command_prefix(raw: str) -> str:
    t = raw
    # strip zero width chars at start
    while t and any(t.startswith(z) for z in ZERO_WIDTH):
        for z in ZERO_WIDTH:
            if t.startswith(z):
                t = t[len(z):]
                break
    return t

def _handle_chat_command(text: str) -> str | None:
    t = _normalize_command_prefix(text.strip())
    if not t.startswith('/'):
        return None
    parts = t[1:].split(None,1)
    cmd = parts[0].lower(); arg = parts[1] if len(parts)>1 else ''
    if cmd in ('help','?'):
        return _help_text(arg if arg else None)
    out = _legacy_logic(cmd, arg)
    if out is None:
        return f'Unbekannt: /{cmd} ( /help )'
    return out

@app.post("/chat", response_model=ChatReply)
async def chat(req: ChatRequest):
    text = req.message.strip()
    if not text:
        return ChatReply(reply="(leer)", history=_history_as_models())
    _chat_history.append({"role": "user", "content": text, "ts": time.time()})
    _save_chat_history()
    # Slash commands (no LLM usage)
    cmd_reply = _handle_chat_command(text)
    if cmd_reply is not None:
        _chat_history.append({"role": "assistant", "content": cmd_reply, "ts": time.time()})
        if len(_chat_history) > MAX_CHAT_MESSAGES:
            del _chat_history[0:len(_chat_history)-MAX_CHAT_MESSAGES]
        _save_chat_history()
        return ChatReply(reply=cmd_reply, history=_history_as_models())
    if to_thread:
        raw = await to_thread.run_sync(lambda: gclient.chat_completion(_build_chat_messages(text)))
    else:
        raw = gclient.chat_completion(_build_chat_messages(text))
    reply = raw.strip()
    # Fallback: if looked like a command but not recognized, append hint
    if _normalize_command_prefix(text).startswith('/') and text.strip().startswith('/') and not reply.lower().startswith('unbekannt:'):
        # Provide subtle hint for misparsed command
        if '/' + text.strip()[1:].split()[0] in ('/help','/analyze','/world.init','/world.tick','/world.spawn','/world.info','/objectives.list','/objectives.set'):
            reply += "\n(Hinweis: Slash-Kommando nicht erkannt? Prüfe verborgene Zeichen / Zero-Width oder sende exakt: /help)"
    if os.getenv('CHAT_DEBUG') == '1':
        reply += f"\n[debug cmd_prefix_norm={_normalize_command_prefix(text)[:20]!r}]"
    _chat_history.append({"role": "assistant", "content": reply, "ts": time.time()})
    if len(_chat_history) > MAX_CHAT_MESSAGES:
        del _chat_history[0:len(_chat_history)-MAX_CHAT_MESSAGES]
    _save_chat_history()
    return ChatReply(reply=reply, history=_history_as_models())

@app.get("/chat/history", response_model=ChatHistoryResp)
async def chat_history():
    return ChatHistoryResp(history=_history_as_models())

@app.get('/chat/stream')
async def chat_stream(message: str):
    """Simplified streaming via Server-Sent Events."""
    import asyncio, json as _json
    user_text = (message or '').strip()
    async def empty_gen():
        yield 'event: done\ndata: {}\n\n'
    if not user_text:
        return StreamingResponse(empty_gen(), media_type='text/event-stream')
    # record user
    _chat_history.append({"role": "user", "content": user_text, "ts": time.time()})
    _save_chat_history()
    # get full reply (no true token stream yet)
    if to_thread:
        raw = await to_thread.run_sync(lambda: gclient.chat_completion(_build_chat_messages(user_text)))
    else:
        raw = gclient.chat_completion(_build_chat_messages(user_text))
    reply = raw.strip()
    _chat_history.append({"role": "assistant", "content": reply, "ts": time.time()})
    if len(_chat_history) > MAX_CHAT_MESSAGES:
        del _chat_history[0:len(_chat_history)-MAX_CHAT_MESSAGES]
    _save_chat_history()
    async def event_gen():
        chunk_size = 60
        for i in range(0, len(reply), chunk_size):
            await asyncio.sleep(0.03)
            delta = reply[i:i+chunk_size]
            yield f"data: {{\"delta\": {_json.dumps(delta)} }}\n\n"
        yield 'event: done\ndata: {}\n\n'
    return StreamingResponse(event_gen(), media_type='text/event-stream')

# ---- Chat -> Proposal Injection ---- #
class ChatToProposalRequest(BaseModel):
    index: int | None = None  # index in history to convert; default last assistant
    filename: str | None = None  # optional target filename hint

class ChatToProposalResp(BaseModel):
    injected: bool
    proposal_id: str | None = None
    error: str | None = None

@app.post('/chat/to-proposal', response_model=ChatToProposalResp, dependencies=[Depends(api_key_guard)])
async def chat_to_proposal(req: ChatToProposalRequest):
    # find candidate assistant message
    if not _chat_history:
        return ChatToProposalResp(injected=False, error='kein Verlauf')
    idx: int | None = req.index
    assistant_indices = [i for i, m in enumerate(_chat_history) if m.get('role') == 'assistant']
    if not assistant_indices:
        return ChatToProposalResp(injected=False, error='keine assistant Nachricht')
    if idx is None:
        idx = assistant_indices[-1]
    if idx < 0 or idx >= len(_chat_history):
        return ChatToProposalResp(injected=False, error='Index ungültig')
    msg = _chat_history[idx]
    if msg.get('role') != 'assistant':
        return ChatToProposalResp(injected=False, error='gewählter Eintrag ist nicht assistant')
    content = str(msg.get('content',''))
    if not content.strip():
        return ChatToProposalResp(injected=False, error='Inhalt leer')
    # derive title & rationale
    first_line = content.strip().splitlines()[0][:120]
    title = first_line if len(first_line) > 10 else (first_line + ' Verbesserung')
    rationale = content[:800]
    # naive diff placeholder
    filename = req.filename or 'README.md'
    diff_body_comment = '\n'.join('# ' + l for l in content.strip().splitlines()[:25])
    diff = f"--- a/{filename}\n+++ b/{filename}\n@@\n# Chat-derived suggestion\n{diff_body_comment}\n"
    from ..core.models import PatchProposal, Score
    pid = f"chat{int(time.time())}"
    proposal = PatchProposal(id=pid, title=title, description=rationale[:400], diff=diff[:20000], rationale=rationale, risk_note='chat-derived')
    proposal.score = Score(clarity=0.55, impact=0.55, risk=0.45, effort=0.5)
    orch.approvals.submit(proposal)
    return ChatToProposalResp(injected=True, proposal_id=pid)

# ---- Root helper ---- #
class RootResp(BaseModel):
    status: str
    message: str
    hint: str
    endpoints: List[str]

# JSON API meta at /api
@app.get("/api", response_model=RootResp)
async def api_root():
    paths: List[str] = []
    for r in app.routes:
        p = getattr(r, 'path', None)
        if isinstance(p, str):
            paths.append(p)
    paths = sorted(set(paths))
    return RootResp(
        status="ok",
        message="Evolution Sandbox API",
        hint="Use /help or /meta (chat) | /world/state for world JSON",
        endpoints=paths,
    )

# ---- World State Endpoint ---- #
class WorldStateResp(BaseModel):
    w: int
    h: int
    ticks: int
    controlled: str | None
    entities_total: int
    entities: List[Dict[str, Any]]

@app.get('/world/state', response_model=WorldStateResp)
async def world_state():
    ents = sim_world.STATE.get('entities', [])
    return WorldStateResp(
        w=sim_world.STATE.get('w', 0),
        h=sim_world.STATE.get('h', 0),
        ticks=sim_world.STATE.get('ticks', 0),
        controlled=sim_world.STATE.get('controlled'),
        entities_total=len(ents),
        entities=[e for e in ents[:100]],
    )

# ---- Metrics Endpoint ---- #
class MetricsResp(BaseModel):
    uptime_s: float
    proposals_generated: int
    proposals_applied: int
    proposals_undone: int
    acceptance_rate: float
    last_analysis_duration_ms: float
    total_diff_bytes_applied: int
    total_files_touched: int
    last_apply_ts: float | None
    last_analysis_ts: float | None
    last_index_build_ts: float | None = None
    index_file_count: int = 0
    index_total_bytes: int = 0

@app.get('/metrics', response_model=MetricsResp)
async def metrics_endpoint():
    try:
        from ..core import metrics as _metrics
        data = _metrics.export_metrics()
        return MetricsResp(**data)  # type: ignore[arg-type]
    except Exception as e:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"metrics_error: {e}")

# ---- Code Indexing Endpoints ---- #
class IndexBuildResp(BaseModel):
    built: bool
    files: int
    bytes: int
    took_ms: float

@app.post('/index/build', response_model=IndexBuildResp, dependencies=[Depends(api_key_guard)])
async def index_build():
    import time as _t
    start = _t.time()
    idx = code_index.ensure_index(repo_root)
    idx.build(repo_root)
    took = (_t.time()-start)*1000
    return IndexBuildResp(built=True, files=len(idx.files), bytes=idx.total_bytes, took_ms=took)

class IndexMatch(BaseModel):
    file: str
    score: int

class IndexSearchResp(BaseModel):
    query: str
    matches: List[IndexMatch]
    took_ms: float

@app.get('/index/search', response_model=IndexSearchResp)
async def index_search(q: str):
    import time as _t
    start = _t.time()
    idx = code_index.ensure_index(repo_root)
    ranked = idx.search_tokens(q)
    took = (_t.time()-start)*1000
    return IndexSearchResp(query=q, matches=[IndexMatch(file=f, score=s) for f,s in ranked], took_ms=took)

class SemanticMatch(BaseModel):
    file: str
    score: float

class SemanticSearchResp(BaseModel):
    query: str
    matches: List[SemanticMatch]
    took_ms: float
    enabled: bool

@app.get('/index/semantic', response_model=SemanticSearchResp)
async def index_semantic(q: str, limit: int = 10):
    import time as _t, os as _os
    start = _t.time()
    idx = code_index.ensure_index(repo_root)
    enabled = _os.getenv('ENABLE_EMBED_INDEX') == '1'
    ranked = idx.semantic_search(q, limit=limit) if enabled else []
    took = (_t.time()-start)*1000
    return SemanticSearchResp(query=q, matches=[SemanticMatch(file=f, score=float(s)) for f,s in ranked], took_ms=took, enabled=enabled)

class IndexSnippetResp(BaseModel):
    file: str
    head: str
    tail: str
    size: int

@app.get('/index/snippet', response_model=IndexSnippetResp)
async def index_snippet(file: str):
    idx = code_index.ensure_index(repo_root)
    text = idx.files.get(file)
    if text is None:
        raise HTTPException(status_code=404, detail='not indexed')
    head = text[:800]
    tail = text[-400:] if len(text) > 1200 else ''
    return IndexSnippetResp(file=file, head=head, tail=tail, size=len(text))

# Serve static assets if present
static_dir = repo_root / 'static'
static_dir.mkdir(exist_ok=True)
app.mount('/static', StaticFiles(directory=str(static_dir)), name='static')

LANDING_HTML = """<!DOCTYPE html><html lang='de'><head><meta charset='utf-8'/><title>Evolution Sandbox</title>
<style>body{font-family:system-ui,Arial,sans-serif;margin:32px;max-width:880px;line-height:1.4;background:#111;color:#eee}code,pre{background:#222;padding:2px 4px;border-radius:3px}a{color:#6cf}h1{margin-top:0}section{margin-bottom:2rem}footer{margin-top:3rem;font-size:.8rem;opacity:.6}</style>
</head><body>
<h1>Evolution Sandbox API</h1>
<p>Diese Seite ist ein minimales Landing. Die JSON-Variante findest du unter <code>/api</code>. Schneller Test per JavaScript unten.</p>
<section><h2>Endpoints</h2><ul>
<li>POST <code>/chat</code> – Chat / Commands</li>
<li>GET <code>/chat/history</code></li>
<li>GET <code>/chat/stream?message=Hallo</code></li>
<li>POST <code>/chat/to-proposal</code></li>
<li>GET <code>/api</code> – Meta JSON</li>
<li>UI: <a href="/static/chat.html">/static/chat.html</a></li>
<li>Standard FastAPI Docs: <a href="/docs">/docs</a></li>
</ul></section>
<section><h2>Quick Console Test</h2>
<pre><code>fetch('/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message:'/meta'})})
    .then(r=>r.json()).then(console.log)</code></pre>
</section>
<section><h2>Slash Hilfe</h2>
<pre><code>fetch('/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message:'/help'})})
    .then(r=>r.json()).then(x=>console.log(x.reply))</code></pre>
</section>
<footer>Evolution Sandbox &middot; <a href="/api">/api</a></footer>
<script>console.log('Landing ready');</script>
</body></html>"""

@app.get('/', response_class=HTMLResponse)
async def root_html():
        return HTMLResponse(content=LANDING_HTML, status_code=200)

# --- Additional utility endpoints --- #
class HealthResp(BaseModel):
    status: str
    cycle: int
    objectives: int
    pending: int
    variant: str
    rate_limit: int

@app.get('/health', response_model=HealthResp)
async def health():
    return HealthResp(status='ok', cycle=orch.state.cycle, objectives=len(orch.state.objectives), pending=len(orch.list_pending()), variant='go', rate_limit=RATE_LIMIT_PER_MIN)

@app.get('/chat')
class ChatGetUsage(BaseModel):
    error: str
    why: str
    example_request: Dict[str, str]
    streaming: str
    docs: str

@app.get('/chat', response_model=ChatGetUsage)
async def chat_usage():
    return ChatGetUsage(
        error='Method Not Allowed: verwende POST /chat',
        why='GET wurde abgelehnt, weil Endpoint nur POST für Nachrichten akzeptiert.',
        example_request={'message':'/meta'},
        streaming='Nutze /chat/stream?message=Hallo für SSE (GET).',
        docs='/docs'
    )

# ---- UI Suggestions (dynamic) ---- #
class SuggestionItem(BaseModel):
    label: str
    cmd: str
    category: str | None = None
    kind: str | None = None  # 'slash' | 'prompt'
    hint: str | None = None

class SuggestionsResp(BaseModel):
    items: List[SuggestionItem]
    generated: float

def _build_suggestions() -> List[SuggestionItem]:
    # Curated core suggestions (extendable later or made configurable)
    base: List[tuple[str,str,str,str|None]] = [
        ('Hilfe','/help','System',None),
        ('Ziele anzeigen','/objectives.list','Ziele',None),
        ('Analyse','/analyze','Analyse',None),
        ('Pending','/pending','System','Listet offene Vorschläge'),
        ('KB Liste','/kb.list','Knowledge',None),
        ('KB Save','/kb.save','Knowledge',None),
        ('Meta','/meta','System',None),
        ('Energy','/energy.show','Energy',None),
        ('Memory Compress','/memory.compress','Reflexion','Komprimiert langen Chat Verlauf'),
        ('World Init','/world.init 20 12','World','Initialisiert 20x12'),
        ('World Spawn','/world.spawn agent alpha','World','Agent erzeugen'),
        ('World Ents','/world.ents','World','Listet Entities'),
        ('World Tick 5','/world.tick 5','World','Sim 5 Schritte'),
        ('Verbesser Doku','Bitte analysiere README und schlage gezielte Verbesserungen für Onboarding vor.','Prompt','Natürliche Sprache'),
        ('Refactor Hinweis','Nenne 3 interne Refactoring-Ziele mit kurzer Begründung.','Prompt','Natürliche Sprache')
    ]
    items: List[SuggestionItem] = []
    for label, cmd, cat, hint in base:
        kind = 'slash' if cmd.startswith('/') else 'prompt'
        items.append(SuggestionItem(label=label, cmd=cmd, category=cat, kind=kind, hint=hint))
    return items

# ---- UI Command Catalog ---- #
class CommandItem(BaseModel):
    command: str
    label: str
    description: str | None = None

class CommandCategoryModel(BaseModel):
    name: str
    items: List[CommandItem]

class CommandsCatalogResp(BaseModel):
    categories: List[CommandCategoryModel]
    count: int
    generated: float

@app.get('/ui/commands', response_model=CommandsCatalogResp)
async def ui_commands_catalog():
    cats: List[CommandCategoryModel] = []
    for cat, pairs in COMMAND_CATEGORIES.items():
        entries: List[CommandItem] = []
        for cmd, desc in pairs:
            entries.append(CommandItem(command='/' + cmd, label=cmd, description=desc))
        cats.append(CommandCategoryModel(name=cat, items=entries))
    total = sum(len(c.items) for c in cats)
    return CommandsCatalogResp(categories=cats, count=total, generated=time.time())

@app.get('/ui/suggestions', response_model=SuggestionsResp)
async def ui_suggestions():
    return SuggestionsResp(items=_build_suggestions(), generated=time.time())

# ---- Structured analysis JSON & injection ---- #
class AnalysisResp(BaseModel):
    suggestions: List[Dict[str, Any]]
    count: int

@app.get('/analysis/json', response_model=AnalysisResp)
async def analysis_json():
    if not _last_analysis:
        _legacy_logic('analyze','')  # trigger analyze once
    return AnalysisResp(suggestions=_last_analysis, count=len(_last_analysis))

class InjectReq(BaseModel):
    id: str
    title: str | None = None
    rationale: str | None = None
    diff_hint: str | None = None

class InjectResp(BaseModel):
    injected: bool
    proposal_id: str | None = None
    error: str | None = None

@app.post('/analysis/inject', response_model=InjectResp, dependencies=[Depends(api_key_guard)])
async def analysis_inject(req: InjectReq):
    try:
        base_title = (req.title or req.id or 'Suggestion')[:120]
        rationale = (req.rationale or '')[:600]
        diff_body_comment = ''
        if req.diff_hint:
            diff_body_comment = '\n'.join('# '+l for l in req.diff_hint.splitlines()[:40])
        filename = 'README.md'
        diff = f"--- a/{filename}\n+++ b/{filename}\n@@\n# Injected suggestion {req.id}\n{diff_body_comment}\n"
        from ..core.models import PatchProposal, Score
        pid = f"inj{int(time.time())}"
        proposal = PatchProposal(id=pid, title=base_title, description=rationale or base_title, diff=diff[:20000], rationale=rationale or '', risk_note='analysis-inject')
        proposal.score = Score(clarity=0.6, impact=0.6, risk=0.4, effort=0.5)
        orch.approvals.submit(proposal)
        return InjectResp(injected=True, proposal_id=pid)
    except Exception as e:
        return InjectResp(injected=False, error=str(e)[:300])

# ---- Contextual suggestion endpoint (dynamic buttons v2) ---- #
class ContextSuggestResp(BaseModel):
    items: List[SuggestionItem]
    reason: str

@app.get('/ui/context-suggestions', response_model=ContextSuggestResp)
async def context_suggestions():
    recent_cmds = [m.get('content','') for m in _chat_history[-12:] if isinstance(m.get('content',''), str) and str(m.get('content','')).startswith('/')]
    objs = orch.state.objectives[:5]
    dynamic: List[SuggestionItem] = []
    if not any('/analyze' in c for c in recent_cmds):
        dynamic.append(SuggestionItem(label='Analyse jetzt', cmd='/analyze', category='Analyse'))
    if not any('/cycle' in c for c in recent_cmds):
        dynamic.append(SuggestionItem(label='Neuer Zyklus', cmd='/cycle', category='System'))
    if objs and 'test' not in ' '.join(o.lower() for o in objs):
        dynamic.append(SuggestionItem(label='Ziel Tests hinzufügen', cmd='/objectives.set ' + '; '.join(objs + ['Tests erhöhen']), category='Ziele', hint='Erweitert Ziele um Tests'))
    if len(orch.list_pending())>0:
        dynamic.append(SuggestionItem(label='Pending anzeigen', cmd='/pending', category='System'))
    if not dynamic:
        dynamic.append(SuggestionItem(label='Hilfe', cmd='/help', category='System'))
    reason = 'Basierend auf letzten Kommandos & Objectives.'
    return ContextSuggestResp(items=dynamic, reason=reason)

# ---- Reply suggestions (next-step buttons based on last assistant msg & state) ---- #
@app.get('/ui/reply-suggestions', response_model=ContextSuggestResp)
async def reply_suggestions():
    """Generate short follow-up suggestion buttons tailored to latest assistant output and current state.

    Heuristic + optional LLM (guarded by REPLY_SUGGEST_LLM=1) to keep fast local default.
    """
    last_assistant = ''
    for m in reversed(_chat_history):  # find last assistant message
        if m.get('role') == 'assistant':
            last_assistant = str(m.get('content',''))
            break
    dynamic: List[SuggestionItem] = []
    recent_cmds = [c for c in (m.get('content','') for m in _chat_history[-10:]) if isinstance(c,str) and c.startswith('/')]
    # Core follow-up command style suggestions
    if '/analyze' not in recent_cmds:
        dynamic.append(SuggestionItem(label='Analyse vertiefen', cmd='/analyze', category='Analyse', hint='Neue Codebasis Analyse'))
    if '/cycle' not in recent_cmds:
        dynamic.append(SuggestionItem(label='Zyklus starten', cmd='/cycle', category='System', hint='Generiere + bewerte Vorschläge'))
    if '/pending' not in recent_cmds and len(orch.list_pending())>0:
        dynamic.append(SuggestionItem(label='Pending anzeigen', cmd='/pending', category='System'))
    if '/world.tick' not in recent_cmds and 'world' in last_assistant.lower():
        dynamic.append(SuggestionItem(label='World Tick 3', cmd='/world.tick 3', category='World'))
    # Prompt style (natural language) suggestions derived heuristically
    nl_prompts: List[str] = []
    if last_assistant:
        lower = last_assistant.lower()
        if 'analyse' in lower or 'analysis' in lower:
            nl_prompts.append('Bitte fasse die wichtigsten Risiken in 3 Bullet Points zusammen.')
            nl_prompts.append('Gib mir eine priorisierte Liste mit maximal 5 konkreten nächsten Schritten.')
        if 'proposal' in lower or 'vorschlag' in lower:
            nl_prompts.append('Bewerte die vorgeschlagenen Änderungen nach Nutzen vs. Aufwand in einer kleinen Tabelle.')
        if 'world' in lower or 'entity' in lower:
            nl_prompts.append('Schlage 2 sinnvolle World Entitäten zusätzlich vor und begründe kurz.')
    if not nl_prompts:
        nl_prompts = [
            'Fasse die letzten Punkte in 2 kurzen Sätzen für ein Changelog zusammen.',
            'Welche Qualitäts- oder Sicherheitslücke ist aktuell am größten? Antworte kurz.'
        ]
    for p in nl_prompts[:3]:
        dynamic.append(SuggestionItem(label=p[:42] + ('…' if len(p)>42 else ''), cmd=p, category='Prompt', kind='prompt', hint='KI Prompt'))
    # Optional LLM enrichment (adds up to 2 extra natural prompts)
    if os.getenv('REPLY_SUGGEST_LLM') == '1' and gclient:
        try:
            prompt = ('Erzeuge 2 sehr kurze sinnvolle Follow-Up Prompts (je < 90 Zeichen) für einen Nutzer, basierend auf: ' + last_assistant[:800])
            # reuse internal builder to satisfy type expectations
            enrich_msgs = _build_chat_messages(prompt)
            if to_thread:
                raw = await to_thread.run_sync(lambda: gclient.chat_completion(enrich_msgs))
            else:
                raw = gclient.chat_completion(enrich_msgs)
            for line in raw.splitlines():
                line=line.strip('- *\t ')[:160]
                if not line: continue
                if len([d for d in dynamic if d.kind=='prompt'])>=6: break
                dynamic.append(SuggestionItem(label=line[:42]+('…' if len(line)>42 else ''), cmd=line, category='Prompt', kind='prompt'))
        except Exception:  # pragma: no cover
            pass
    reason = 'Abgeleitet aus letzter Assistant Nachricht & Zustand.'
    return ContextSuggestResp(items=dynamic[:10], reason=reason)

# ---- Improve suggestions (JSON + inject) ---- #
class ImproveResp(BaseModel):
    items: List[Dict[str, Any]]
    count: int

@app.get('/improve/json', response_model=ImproveResp)
async def improve_json():
    # trigger scan if empty
    if not _last_improve:
        _legacy_logic('improve.scan','')
    return ImproveResp(items=_last_improve, count=len(_last_improve))

class ImproveInjectReq(BaseModel):
    id: str

@app.post('/improve/inject', response_model=InjectResp, dependencies=[Depends(api_key_guard)])
async def improve_inject(req: ImproveInjectReq):
    match = None
    for s in _last_improve:
        if s.get('id') == req.id:
            match = s; break
    if not match:
        return InjectResp(injected=False, error='id not found')
    from ..core.models import PatchProposal, Score
    title = str(match.get('title','Suggestion'))[:120]
    rationale = str(match.get('rationale',''))[:600]
    diff_hint = str(match.get('diff_hint',''))
    diff_body_comment = '\n'.join('# '+l for l in diff_hint.splitlines()[:40]) if diff_hint else '# (placeholder)'
    filename='README.md'
    diff = f"--- a/{filename}\n+++ b/{filename}\n@@\n# Improve injected {req.id}\n{diff_body_comment}\n"
    pid = f"imp{int(time.time())}"
    proposal = PatchProposal(id=pid, title=title, description=rationale or title, diff=diff[:20000], rationale=rationale, risk_note='improve-inject')
    proposal.score = Score(clarity=0.58, impact=0.62, risk=0.42, effort=0.5)
    orch.approvals.submit(proposal)
    return InjectResp(injected=True, proposal_id=pid)

# ---- Proposals (structured endpoints for UI) ---- #
class PendingProposal(BaseModel):
    id: str
    title: str
    clarity: float | None = None
    impact: float | None = None
    risk: float | None = None
    effort: float | None = None
    composite: float | None = None

class PendingListResp(BaseModel):
    items: List[PendingProposal]
    count: int

@app.get('/proposals/pending', response_model=PendingListResp)
async def proposals_pending():
    items: List[PendingProposal] = []
    for p in orch.list_pending():
        sc = p.score
        items.append(PendingProposal(
            id=p.id,
            title=p.title[:160],
            clarity=getattr(sc,'clarity',None),
            impact=getattr(sc,'impact',None),
            risk=getattr(sc,'risk',None),
            effort=getattr(sc,'effort',None),
            composite=getattr(sc,'composite',None) if sc else None
        ))
    return PendingListResp(items=items, count=len(items))

class PreviewResp(BaseModel):
    id: str
    diff: str

@app.get('/proposals/preview/{pid}', response_model=PreviewResp)
async def proposal_preview(pid: str):
    diff = orch.preview(pid)
    return PreviewResp(id=pid, diff=diff)

class ApplyReq(BaseModel):
    id: str

class ApplyResp(BaseModel):
    applied: bool
    id: str | None = None
    file: str | None = None
    error: str | None = None

@app.post('/proposals/apply', response_model=ApplyResp, dependencies=[Depends(api_key_guard)])
async def proposal_apply(req: ApplyReq):
    try:
        file_or_msg = orch.apply_after_approval(req.id, dry_run=False)
        return ApplyResp(applied=True, id=req.id, file=file_or_msg)
    except Exception as e:  # pragma: no cover
        return ApplyResp(applied=False, id=req.id, error=str(e)[:300])

class UndoResp(BaseModel):
    undone: bool
    id: str | None = None

@app.post('/proposals/undo', response_model=UndoResp, dependencies=[Depends(api_key_guard)])
async def proposal_undo():
    res = orch.undo_last()
    return UndoResp(undone=bool(res), id=res)

# --- Legacy simple apply endpoint (compat) --- #
class LegacyApplyResp(BaseModel):
    applied: bool
    id: str | None = None
    result: str | None = None
    error: str | None = None

@app.post('/apply/{proposal_id}', response_model=LegacyApplyResp)
async def legacy_apply(proposal_id: str, x_api_key: str | None = Header(default=None)):
    # honour role guard (write)
    api_key_guard(x_api_key, required='write')
    try:
        file_or_msg = orch.apply_after_approval(proposal_id, dry_run=False)
    except Exception as e:  # pragma: no cover
        return JSONResponse(status_code=400, content={'applied': False, 'id': proposal_id, 'error': str(e)[:300]})
    return LegacyApplyResp(applied=True, id=proposal_id, result=file_or_msg)
