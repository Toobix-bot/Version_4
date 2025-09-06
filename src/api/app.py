from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pathlib import Path
from typing import List, Any, Dict, TypedDict
from ..core.orchestrator import Orchestrator
from ..io.groq_client import GroqClient
from ..core.twin import TwinCoordinator, SnapshotManager
from ..core.analysis import analyze_repository
import time, asyncio
try:
    from anyio import to_thread
except Exception:  # fallback if anyio import pattern changes
    to_thread = None  # type: ignore

app = FastAPI(title="Evolution Sandbox API", version="0.1.0")
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
    'Personas': [('personas.list','Alle & aktive'),('personas.set','Aktive setzen'),('personas.add','Neue Persona'),('personas.grow','Synthese'),('personas.save','Speichern'),('personas.load','Laden')],
    'Multi': [('multi','Mehrperspektive'),('multi.consensus','Konsens'),('multi.conflicts','Konflikte'),('multi.vote','Voting')],
    'Reflexion': [('reflect','Reflexion'),('reflect.conflicts','Konflikt-Reflex'),('reflections.list','Liste'),('reflections.evaluate','Bewerten'),('memory.compress','Verlauf komprimieren')],
    'Knowledge': [('kb.save','Speichern'),('kb.list','Auflisten'),('kb.search','Suchen'),('kb.get','Zeigen'),('kb.inject','In Chat injizieren')],
    'User': [('me.show','User-Modell'),('me.set','Setzen'),('me.addinterest','Interessen +'),('suggest','Themen Vorschläge')],
    'Notebook': [('nb.new','Neu'),('nb.list','Liste'),('nb.show','Zeigen'),('nb.add','Zelle hinzufügen'),('nb.tag','Tags'),('nb.save','Persist')],
    'Energy': [('energy.show','Status'),('energy.tick','Tick')],
    'Self': [('self.tick','Selbst-Tick'),('self.show','Self anzeigen')],
    'Coach': [('coach.on','Auto an'),('coach.off','Auto aus')],
    'Analyse': [('analyze','Repo Analyse'),('analysis.last','Letzte Analyse')]
}

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
        for m in raw[-MAX_CHAT_MESSAGES:]:
            if isinstance(m, dict) and 'role' in m and 'content' in m:
                _chat_history.append({
                    'role': str(m.get('role','user')),
                    'content': str(m.get('content','')),
                    'ts': float(m.get('ts', time.time()))
                })

def _load_knowledge():
    raw = _load_json(repo_logs / 'knowledge.json')
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict) and 'id' in e:
                _knowledge_index.append(e)

def _load_reflections():
    raw = _load_json(repo_logs / 'reflections.json')
    if isinstance(raw, list):
        for r in raw:
            if isinstance(r, dict):
                _reflections.append(r)

def _load_long_memory():
    raw = _load_json(repo_logs / 'long_memory.json')
    if isinstance(raw, list):
        for r in raw:
            if isinstance(r, dict):
                _long_memory.append(r)

def _load_notebooks():
    raw = _load_json(repo_logs / 'notebooks.json')
    if isinstance(raw, list):
        for n in raw:
            if isinstance(n, dict):
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
                suggestions = analyze_repository(repo_root, orch.state.objectives, gclient)
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
            if not norm: return f"Analyse leer ({took:.0f}ms)."
            return f"Analyse {len(norm)} Vorschläge ({took:.0f}ms): " + ', '.join(f"{d['id']}:{d['title'][:18]}" for d in norm[:6])
        if cmd == 'analysis.last':
            if not _last_analysis: return 'Noch keine Analyse.'
            return 'Letzte Analyse: ' + ', '.join(f"{d['id']}:{d['title'][:18]}" for d in _last_analysis[:8])
    except Exception as e:  # pragma: no cover
        return f'Command Fehler: {e}'[:200]
    return None

def _handle_chat_command(text: str) -> str | None:
    t = text.strip()
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

@app.post('/chat/to-proposal', response_model=ChatToProposalResp)
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

@app.get("/", response_model=RootResp)
async def root():
    return RootResp(
        status="ok",
        message="Evolution Sandbox API",
        hint="POST /chat  JSON: { 'message': '/meta' }",
        endpoints=[
            "POST /chat",
            "GET /chat/history",
            "GET /chat/stream?message=...",
            "POST /chat/to-proposal"
        ]
    )
