from __future__ import annotations
from fastapi import FastAPI, Response, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from pathlib import Path
from typing import List, Optional, Any, Dict, cast
from ..core.orchestrator import Orchestrator
from ..io.groq_client import GroqClient
from ..core.twin import TwinCoordinator, SnapshotManager
from ..core.analysis import analyze_repository
import time
try:
    from anyio import to_thread
except Exception:  # fallback if anyio import pattern changes
    to_thread = None  # type: ignore

app = FastAPI(title="Evolution Sandbox API", version="0.1.0")
repo_root = Path(__file__).resolve().parent.parent.parent
orch = Orchestrator(repo_root=repo_root)
gclient = GroqClient()
twin = TwinCoordinator(repo_root=repo_root)
snaps = SnapshotManager(repo_root=repo_root)
_chat_history: List[Dict[str, Any]] = []
MAX_CHAT_MESSAGES = 50
CHAT_HISTORY_PATH = repo_root / 'logs' / 'chat_history.json'

def _load_chat_history():
    try:
        if not CHAT_HISTORY_PATH.exists():
            return
        import json
        raw_loaded: Any = json.loads(CHAT_HISTORY_PATH.read_text(encoding='utf-8'))
        if not isinstance(raw_loaded, list):
            return
        # iterate tail only
        for item_any in list(raw_loaded)[-MAX_CHAT_MESSAGES:]:  # type: ignore
            if not isinstance(item_any, dict):
                continue
            item: Dict[str, Any] = cast(Dict[str, Any], item_any)
            role_val: Any = item.get('role', 'user')
            content_val: Any = item.get('content', '')
            ts_val: Any = item.get('ts', 0.0)
            role_str = str(role_val) if not isinstance(role_val, (bytes, bytearray)) else role_val.decode('utf-8', 'ignore')
            content_str = str(content_val) if not isinstance(content_val, (bytes, bytearray)) else content_val.decode('utf-8', 'ignore')
            if isinstance(ts_val, (int, float)):
                ts_f = float(ts_val)
            elif isinstance(ts_val, str):
                try:
                    ts_f = float(ts_val.strip())
                except Exception:
                    ts_f = 0.0
            else:
                ts_f = 0.0
            _chat_history.append({'role': role_str, 'content': content_str, 'ts': ts_f})
    except Exception:  # pragma: no cover
        return

def _save_chat_history():
    try:
        CHAT_HISTORY_PATH.parent.mkdir(exist_ok=True)
        import json
        CHAT_HISTORY_PATH.write_text(json.dumps(_chat_history[-MAX_CHAT_MESSAGES:], ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:  # pragma: no cover
        pass

_load_chat_history()

class Proposal(BaseModel):
    id: str
    title: str
    description: str
    score: float | None

class CycleResponse(BaseModel):
    cycle: int
    proposals: List[Proposal]

class RawLLMRequest(BaseModel):
    prompt: str
    system: Optional[str] = None

class RawLLMResponse(BaseModel):
    content: str
    truncated: bool
    model: str

class HealthResp(BaseModel):
    status: str
    cycle: int
    pending: int

class GroqCheckResp(BaseModel):
    api_key: bool
    mode: str | None = None
    message: str | None = None
    response_sample: str | None = None

class TwinCycleResp(BaseModel):
    cycles: int
    produced: List[str]

class TwinChangedResp(BaseModel):
    changed: List[str]

class TwinPromoteResp(BaseModel):
    promoted: List[str]

class SnapshotMetaResp(BaseModel):
    id: str
    label: str
    timestamp: float

class SnapshotListResp(BaseModel):
    snapshots: List[SnapshotMetaResp]

class SnapshotRestoreResp(BaseModel):
    restored: bool
    id: str

class TwinResetResp(BaseModel):
    reset: bool
    error: str | None = None

class MetaResp(BaseModel):
    parse_meta: dict[str, object]
    cycle: int
    pending: int
    twin_changed: int
    snapshots: int
    model: str | None
    objectives: List[str]
    chat_messages: int

@app.post("/cycle", response_model=CycleResponse)
async def run_cycle():
    scored = orch.cycle(dry_run=True)
    return CycleResponse(
        cycle=orch.state.cycle,
        proposals=[Proposal(id=p.id, title=p.title, description=p.description, score=p.score.composite if p.score else None) for p in scored]
    )

@app.get("/pending", response_model=List[Proposal])
async def get_pending():
    pending = orch.list_pending()
    return [Proposal(id=p.id, title=p.title, description=p.description, score=p.score.composite if p.score else None) for p in pending]

@app.post("/apply/{proposal_id}")
async def apply(proposal_id: str):
    result = orch.apply_after_approval(proposal_id, dry_run=False)
    return {"applied": proposal_id, "result": result}

@app.get("/preview/{proposal_id}")
async def preview(proposal_id: str):
    try:
        diff = orch.preview(proposal_id)
        return {"proposal_id": proposal_id, "diff": diff}
    except Exception as e:
        return {"error": str(e)}

@app.post("/undo")
async def undo():
    res = orch.undo_last()
    return {"undone": res}

@app.get("/health", response_model=HealthResp)
async def health():
    return HealthResp(status="ok", cycle=orch.state.cycle, pending=len(orch.list_pending()))

@app.get("/groq-check", response_model=GroqCheckResp)
async def groq_check():
    if not gclient.cfg.api_key:
        return GroqCheckResp(api_key=False, mode="no-key", message="Kein API_KEY gesetzt.")
    # very small test prompt
    from ..core.models import Message
    msg = [Message(role="user", content="Sag nur OK.")]
    out = gclient.chat_completion(msg)
    truncated = out.strip()[:200]
    return GroqCheckResp(api_key=True, response_sample=truncated, mode="ok")

# ------------- Twin / Sandbox ------------- #
@app.post("/twin/sandbox-cycle", response_model=TwinCycleResp)
async def twin_sandbox_cycle(cycles: int = 1):
    start = time.time()
    try:
        if to_thread:
            produced = await to_thread.run_sync(lambda: twin.sandbox_cycle(cycles=cycles, dry_run=True))
        else:
            produced = twin.sandbox_cycle(cycles=cycles, dry_run=True)
        return TwinCycleResp(cycles=cycles, produced=produced)
    except Exception as e:  # pragma: no cover
        return TwinCycleResp(cycles=cycles, produced=[f"ERROR: {e}"])
    finally:
        dur = (time.time() - start) * 1000
        # lightweight timing log (append)
        try:
            (repo_root / 'logs' / 'api_timing.log').parent.mkdir(exist_ok=True)
            with (repo_root / 'logs' / 'api_timing.log').open('a', encoding='utf-8') as fh:
                fh.write(f"sandbox_cycle cycles={cycles} duration_ms={dur:.1f}\n")
        except Exception:
            pass

@app.get("/twin/changed", response_model=TwinChangedResp)
async def twin_changed():
    return TwinChangedResp(changed=twin.diff_changed_files())

@app.post("/twin/promote", response_model=TwinPromoteResp)
async def twin_promote(files: Optional[List[str]] = Body(default=None)):
    try:
        if to_thread:
            promoted = await to_thread.run_sync(lambda: twin.promote(files=files, dry_run=False))
        else:
            promoted = twin.promote(files=files, dry_run=False)
        return TwinPromoteResp(promoted=promoted)
    except Exception as e:  # pragma: no cover
        return TwinPromoteResp(promoted=[f"ERROR: {e}"])

@app.post("/twin/reset", response_model=TwinResetResp)
async def twin_reset():
    try:
        if to_thread:
            await to_thread.run_sync(lambda: twin.reset_sandbox())
        else:
            twin.reset_sandbox()
        return TwinResetResp(reset=True)
    except Exception as e:  # pragma: no cover
        return TwinResetResp(reset=False, error=str(e))

# ------------- Snapshots ------------- #
@app.post("/snapshot/create", response_model=SnapshotMetaResp)
async def snapshot_create(label: str = Body(embed=True)):  # label as form/json field
    meta = snaps.create(label)
    return SnapshotMetaResp(id=meta.id, label=meta.label, timestamp=meta.timestamp)

@app.get("/snapshot/list", response_model=SnapshotListResp)
async def snapshot_list():
    out = [SnapshotMetaResp(id=s.id, label=s.label, timestamp=s.timestamp) for s in snaps.list()]
    return SnapshotListResp(snapshots=out)

@app.post("/snapshot/restore/{snap_id}", response_model=SnapshotRestoreResp)
async def snapshot_restore(snap_id: str):
    ok = snaps.restore(snap_id)
    return SnapshotRestoreResp(restored=ok, id=snap_id)

# ------------- Raw LLM Playground ------------- #
@app.post("/llm/raw", response_model=RawLLMResponse)
async def llm_raw(req: RawLLMRequest):
    from ..core.models import Message
    from typing import List as _List
    msgs: _List[Message] = []
    if req.system:
        msgs.append(Message(role="system", content=req.system[:400]))
    msgs.append(Message(role="user", content=req.prompt[:5000]))
    out = gclient.chat_completion(msgs)
    truncated_flag = len(out) > 6000
    if truncated_flag:
        out_disp = out[:6000] + "...<truncated>"
    else:
        out_disp = out
    return RawLLMResponse(content=out_disp, truncated=truncated_flag, model=gclient.cfg.model)

INDEX_HTML = """
<!doctype html>
<html lang='de'>
<head><meta charset='utf-8'/><title>Evolution Sandbox</title>
<style>
*{box-sizing:border-box}
:root{--bg:#0d1117;--bg-alt:#161b22;--border:#30363d;--text:#e6edf3;--text-dim:#8b949e;--accent:#238636;--accent-border:#2ea043;--danger:#da3633;--danger-border:#f85149;--code:#30363d;--badge:#30363d;--badge-new:#1f6feb;--shadow:0 4px 12px rgba(0,0,0,.35)}
[data-theme='light']{--bg:#ffffff;--bg-alt:#f6f8fa;--border:#d0d7de;--text:#24292f;--text-dim:#57606a;--accent:#1a7f37;--accent-border:#1f883d;--danger:#cf222e;--danger-border:#ff8182;--code:#f3f4f6;--badge:#eaeef2;--badge-new:#0969da;--shadow:0 4px 10px rgba(0,0,0,.08)}
body{font-family:System-ui,-apple-system,Segoe UI,Arial,sans-serif;margin:0;padding:0;background:var(--bg);color:var(--text);line-height:1.45;--panelGrad:linear-gradient(145deg,var(--bg-alt) 0%,rgba(60,60,60,0.08) 100%)}
header{padding:1rem 1.5rem;background:var(--bg-alt);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:1rem}
main{padding:1.5rem;max-width:1040px;margin:0 auto}
footer{margin-top:3rem;padding:1rem 1.5rem;font-size:.75rem;color:var(--text-dim);border-top:1px solid var(--border);text-align:center}
h1{font-size:1.4rem;margin:0;font-weight:600}
h2{margin-top:2rem;font-size:1.1rem;border-bottom:1px solid #30363d;padding-bottom:.25rem}
button{cursor:pointer;padding:.45rem .9rem;margin:.2rem;background:var(--accent);color:#fff;border:1px solid var(--accent-border);border-radius:6px;font-size:.85rem;transition:.15s background}
button.secondary{background:var(--badge);border-color:var(--border);color:var(--text)}
button.danger{background:var(--danger);border-color:var(--danger-border)}
button:disabled{opacity:.45;cursor:not-allowed}
code{background:var(--code);padding:2px 6px;border-radius:4px;font-size:.75rem}
ul{list-style:none;padding-left:0;margin:0}
li.proposal,div.proposal{background:var(--bg-alt);border:1px solid var(--border);border-radius:8px;margin:.4rem 0;padding:.6rem .75rem;display:flex;flex-direction:column;gap:.35rem;box-shadow:var(--shadow);background-image:var(--panelGrad)}
.row{display:flex;flex-wrap:wrap;gap:1rem;align-items:center}
.status-bar{display:flex;flex-wrap:wrap;gap:1rem;font-size:.8rem;color:var(--text-dim);margin-top:.6rem}
.badge{display:inline-block;background:var(--badge);color:var(--text);padding:2px 6px;border-radius:12px;font-size:.65rem;letter-spacing:.5px;text-transform:uppercase}
.badge.llm{background:var(--badge-new);color:#fff}
.badge.fallback{background:var(--text-dim);color:var(--bg)}
.score{font-weight:600;color:#e3b341}
.id{font-family:monospace;color:#8b949e;font-size:.75rem}
.title-line{display:flex;justify-content:space-between;align-items:center;gap:.5rem}
.actions{display:flex;gap:.4rem;flex-wrap:wrap}
.meta-block{background:var(--bg-alt);border:1px solid var(--border);padding:.75rem 1rem;border-radius:8px;font-size:.75rem;display:flex;flex-direction:column;gap:.35rem;min-width:240px}
/* Diff modal */
.modal{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:100;opacity:0;pointer-events:none;transition:.2s opacity}
.modal.active{opacity:1;pointer-events:auto}
.modal-content{background:var(--bg-alt);border:1px solid var(--border);max-width:900px;width:90%;max-height:80vh;overflow:auto;border-radius:10px;box-shadow:var(--shadow);padding:1rem;font-family:monospace;font-size:.75rem;line-height:1.25}
.diff-line{white-space:pre}
.diff-line.add{background:rgba(46,160,67,.15);color:#3fb950}
.diff-line.del{background:rgba(248,81,73,.18);color:#ff7b72}
.modal-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:.5rem;font-family:System-ui}
.close-btn{background:var(--danger);border:1px solid var(--danger-border)}
.theme-toggle{background:var(--badge);border:1px solid var(--border);color:var(--text)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1rem;margin-top:.75rem}
.panel{background:var(--bg-alt);border:1px solid var(--border);padding:1rem;border-radius:10px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:.6rem}
.panel h3{margin:.2rem 0 .2rem;font-size:.95rem}
.panel small{color:var(--text-dim)}
textarea{width:100%;min-height:120px;resize:vertical;background:var(--code);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:.6rem;font-family:monospace;font-size:.75rem}
input[type=text],input[type=number]{background:var(--code);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:.45rem .6rem;font-size:.8rem;width:100%;}
table{width:100%;border-collapse:collapse;font-size:.7rem}
th,td{padding:.35rem .4rem;border:1px solid var(--border);text-align:left}
th{background:var(--bg-alt)}
tr:nth-child(even){background:rgba(255,255,255,0.02)}
.flex{display:flex;gap:.75rem;flex-wrap:wrap}
.nowrap{white-space:nowrap}
.muted{color:var(--text-dim)}
.success{color:#3fb950}
.warn{color:#d29922}
.danger-t{color:#ff7b72}
.empty{opacity:.6;font-style:italic;padding:.5rem}
a{color:#58a6ff;text-decoration:none}
a:hover{text-decoration:underline}
@media (max-width:640px){header,main{padding:1rem}button{font-size:.75rem}}
</style></head>
<body>
<header>
    <h1>Evolution Sandbox</h1>
        <div class='actions'>
        <button onclick='runCycle()'>Cycle</button>
        <button class='secondary' onclick='loadAll()'>Refresh</button>
        <button class='danger' onclick='undoLast()'>Undo</button>
            <button class='theme-toggle' onclick='toggleTheme()' id='themeBtn' title='Theme Toggle'>☀️/🌙</button>
    </div>
</header>
<main>
    <div class='status-bar' id='status'>Lade...</div>
    <div class='grid' id='metaGrid'></div>
    <h2>Evolution</h2>
    <div class='flex'>
        <div style='flex:2;min-width:330px'>
            <h3>Neue Vorschläge</h3>
            <div id='new' class='list'></div>
            <h3>Pending</h3>
            <div id='pending' class='list'></div>
        </div>
        <div class='panel' style='flex:1;min-width:260px'>
            <h3>Sandbox / Twin</h3>
            <div class='row'>
                <input type='number' id='sandboxCycles' value='1' min='1' style='max-width:90px'>
                <button onclick='runSandbox()'>Sandbox Cycle</button>
            </div>
            <button class='secondary' onclick='listChanged()'>Änderungen anzeigen</button>
            <div id='changedList' class='muted' style='max-height:140px;overflow:auto;font-size:.65rem'></div>
            <button onclick='promoteAll()'>Promote Alle</button>
            <button class='secondary' onclick='resetSandbox()'>Sandbox Reset</button>
            <h3>Snapshots</h3>
            <div class='row'>
                <input type='text' id='snapLabel' placeholder='Label'>
                <button onclick='createSnapshot()'>Save</button>
            </div>
            <div id='snapList' style='max-height:140px;overflow:auto;font-size:.65rem'></div>
        </div>
        <div class='panel' style='flex:1;min-width:300px'>
            <h3>LLM Playground</h3>
            <small>Roh-Prompt direkt an aktuelles Modell (<span id='playModel'>...</span>)</small>
            <textarea id='rawPrompt' placeholder='Frage oder Anweisung...'></textarea>
            <button onclick='sendRaw()'>Senden</button>
            <button class='secondary' onclick='clearRaw()'>Clear</button>
            <div id='rawResult' class='muted' style='white-space:pre-wrap;font-size:.65rem;max-height:220px;overflow:auto'></div>
        </div>
        <div class='panel' style='flex:1;min-width:300px'>
            <h3>Analyse & Ziele</h3>
            <small>Ziele setzen & automatische Ideen generieren.</small>
            <textarea id='objectivesBox' placeholder='Ziel je Zeile'></textarea>
            <div class='row'>
                <button onclick='saveObjectives()'>Ziele Speichern</button>
                <button class='secondary' onclick='runAnalysis()'>Analysieren</button>
            </div>
            <div id='analysisResult' class='muted' style='font-size:.65rem;max-height:220px;overflow:auto'></div>
        </div>
    <div class='panel' style='flex:1.4;min-width:340px;display:flex;flex-direction:column'>
            <h3 style='margin-top:0'>Chat</h3>
            <div id='chatBox' style='background:var(--code);padding:.6rem;border-radius:6px;flex:1;min-height:240px;max-height:520px;overflow:auto;font-size:.7rem;resize:vertical'></div>
            <textarea id='chatInput' placeholder='Frage / Wunsch... (Enter=Send, Shift+Enter=Zeilenumbruch)' style='margin-top:.5rem;min-height:70px;resize:vertical'></textarea>
            <div class='row' style='margin-top:.3rem'>
                <button onclick='sendChat()'>Senden</button>
        <button class='secondary' onclick='streamChat()'>Stream</button>
                <button class='secondary' onclick='loadChat()'>Reload</button>
                <button class='secondary' onclick='chatToProposal()' title='Letzte Assistant-Antwort als Proposal'>→ Proposal</button>
                <button class='secondary' onclick='clearChat()'>Clear Local</button>
            </div>
        </div>
    </div>
        <div id='diffModal' class='modal'>
            <div class='modal-content'>
                <div class='modal-head'>
                    <strong id='diffTitle'>Diff</strong>
                    <div>
                        <button class='secondary' onclick='copyDiff()'>Copy</button>
                        <button class='close-btn' onclick='closeDiff()'>Close</button>
                    </div>
                </div>
                <div id='diffBody'></div>
            </div>
        </div>
</main>
<footer>
    UI verbessert • Quelle: interner FastAPI Endpunkt • <a href='https://github.com/' target='_blank' rel='noopener'>Repo</a>
</footer>
<script>
function el(html){const t=document.createElement('template');t.innerHTML=html.trim();return t.content.firstChild;}
function proposalCard(p, badge){
    return el(`<div class='proposal li proposal'>
        <div class='title-line'>
            <div><span class='id'>${p.id}</span> <span class='score'>${p.score??'?'} </span> – <strong>${p.title}</strong></div>
    <div class='actions'><button onclick="showDiff('${p.id}')">Diff</button><button onclick="applyOne('${p.id}')">Apply</button></div>
        </div>
        <div style='font-size:.75rem;opacity:.85'>${p.description||''}</div>
        <div>${badge}</div>
    </div>`);
}
async function runCycle(){
    const r=await fetch('/cycle',{method:'POST'});const j=await r.json();
    renderNew(j.proposals);
    await loadPending();
    await loadMeta();
}
async function loadPending(){
    const r=await fetch('/pending');const j=await r.json();
    const wrap=document.getElementById('pending');wrap.innerHTML='';
    if(!j.length) wrap.innerHTML='<div class="empty">Keine Pending Vorschläge</div>';
    j.forEach(p=>wrap.appendChild(proposalCard(p,'<span class="badge">pending</span>')));
}
function renderNew(list){
    const wrap=document.getElementById('new');wrap.innerHTML='';
    if(!list.length){wrap.innerHTML='<div class="empty">Keine neuen Vorschläge</div>';return;}
    list.forEach(p=>wrap.appendChild(proposalCard(p,'<span class="badge llm">neu</span>')));
}
async function loadMeta(){
    try{
        const r=await fetch('/meta');const j=await r.json();
        const g=document.getElementById('metaGrid');g.innerHTML='';
        const pm=j.parse_meta||{};
        const box=(title,kv)=>{
            const rows=Object.entries(kv).map(([k,v])=>`<div><code>${k}</code>: ${v}</div>`).join('');
            return `<div class='meta-block'><strong>${title}</strong>${rows}</div>`;
        };
    g.innerHTML=box('Zustand',{cycle:j.cycle,pending:j.pending,changed:j.twin_changed,snapshots:j.snapshots})+box('Parse Meta',pm)+box('LLM',{model:j.model||'?'})+box('Objectives',Object.fromEntries(j.objectives.map((o,i)=>[i,o])));
    document.getElementById('status').textContent=`Cycle ${j.cycle} • Pending ${j.pending} • Changed ${j.twin_changed} • Model ${(j.model||'?')}`;
    document.getElementById('playModel').textContent=j.model||'?';
    }catch(e){document.getElementById('status').textContent='Meta Fehler';}
}
async function applyOne(id){await fetch('/apply/'+id,{method:'POST'});await loadPending();await loadMeta();}
async function undoLast(){await fetch('/undo',{method:'POST'});await loadPending();await loadMeta();}
async function loadAll(){await loadPending();await loadMeta();}
// Sandbox / Twin
async function runSandbox(){
    const c=parseInt(document.getElementById('sandboxCycles').value||'1');
    const r=await fetch('/twin/sandbox-cycle?cycles='+c,{method:'POST'});const j=await r.json();
    listChanged();
}
async function listChanged(){
    const r=await fetch('/twin/changed');const j=await r.json();
    const box=document.getElementById('changedList');
    if(!j.changed.length){box.textContent='Keine Änderungen';return;}
    box.innerHTML=j.changed.map(f=>`<div><label><input type='checkbox' data-chk name='chg' value='${f}' checked> ${f}</label></div>`).join('');
}
async function promoteAll(){
    const sel=[...document.querySelectorAll('input[data-chk]:checked')].map(i=>i.value);
    const r=await fetch('/twin/promote',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sel)});await loadMeta();listChanged();
}
async function resetSandbox(){await fetch('/twin/reset',{method:'POST'});listChanged();}
// Snapshots
async function refreshSnapshots(){
    const r=await fetch('/snapshot/list');const j=await r.json();
    const div=document.getElementById('snapList');
    if(!j.snapshots.length){div.textContent='Keine Snapshots';return;}
    div.innerHTML=j.snapshots.map(s=>`<div><span class='id'>${s.id}</span> ${s.label} <button onclick="restoreSnap('${s.id}')">Restore</button></div>`).join('');
}
async function createSnapshot(){const label=(document.getElementById('snapLabel').value||'Snapshot');await fetch('/snapshot/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label})});refreshSnapshots();}
async function restoreSnap(id){await fetch('/snapshot/restore/'+id,{method:'POST'});loadAll();}
// Raw LLM
async function sendRaw(){
    const prompt=document.getElementById('rawPrompt').value.trim();if(!prompt){return;}
    const r=await fetch('/llm/raw',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt})});const j=await r.json();
    document.getElementById('rawResult').textContent=j.content;}
function clearRaw(){document.getElementById('rawPrompt').value='';document.getElementById('rawResult').textContent='';}
// Objectives & Analysis
async function saveObjectives(){
    const raw=document.getElementById('objectivesBox').value.split(/\\r?\\n/).filter(l=>l.trim());
    await fetch('/objectives',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({objectives:raw})});
    loadMeta();
}
async function runAnalysis(){
    const box=document.getElementById('analysisResult');box.textContent='Analysiere...';
    const r=await fetch('/analyze');const j=await r.json();
    if(!j.suggestions.length){box.textContent='Keine Vorschläge (Analyse leer).';return;}
    box.innerHTML=j.suggestions.map(s=>`<div style='margin-bottom:.4rem'><strong>${s.id}</strong> ${s.title}<br><em>${s.rationale}</em><br><code>${s.diff_hint||''}</code><br><button onclick=\"injectSug('${s.id}')\">→ Pending</button></div>`).join('');
    window._analysisCache=j.suggestions;
}
async function injectSug(id){
    if(!window._analysisCache)return;const s=window._analysisCache.find(x=>x.id===id);if(!s)return;
    const diff=`--- placeholder\n+++ placeholder\n@@\n# ${s.title}\n# ${s.diff_hint}`;
    await fetch('/inject-proposal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:s.title,rationale:s.rationale,diff})});
    loadPending();
}
// Chat
function escapeHtml(t){return t.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function renderMarkdown(raw){
    if(!raw) return '';
    // escape first
    let txt=raw;
    // simple fenced code split (no language detection)
    const parts=txt.split('```');
    for(let i=1;i<parts.length;i+=2){
        parts[i]=`<pre class='code'><code>${escapeHtml(parts[i])}</code></pre>`;
    }
    txt=parts.join('');
    // bold **text**
    txt=txt.replace(/\\*\\*(.+?)\\*\\*/g,'<strong>$1</strong>');
    // inline code `code`
    txt=txt.replace(/`([^`]+)`/g,(m,g)=>`<code>${escapeHtml(g)}</code>`);
    // line breaks to <br>
    txt=txt.replace(/\\n{2,}/g,'<br><br>').replace(/\\n/g,'<br>');
    return txt;
}
function renderChat(hist){
    const box=document.getElementById('chatBox');if(!box)return;
    box.innerHTML=hist.map(m=>{
        const role=m.role==='user';
        const content= m.role==='assistant'? renderMarkdown(m.content): escapeHtml(m.content);
        return `<div style='margin-bottom:6px'><span class='badge' style='background:${role?'#1f6feb':'#30363d'}'>${m.role}</span> <span class='msg'>${content}</span></div>`;
    }).join('');
    box.scrollTop=box.scrollHeight;
}
async function loadChat(){try{const r=await fetch('/chat/history');const j=await r.json();renderChat(j.history||[]);}catch(e){}}
async function sendChat(){
    const ta=document.getElementById('chatInput');if(!ta)return;const msg=ta.value.trim();if(!msg)return;ta.value='';
    // Optimistisches Rendern
    const box=document.getElementById('chatBox');
    if(box){
        box.innerHTML += `<div style='margin-bottom:4px'><span class='badge' style='background:#1f6feb'>user</span> ${msg}</div>`;
        box.scrollTop=box.scrollHeight;
    }
    try{
        const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg})});
        if(!r.ok){throw new Error('HTTP '+r.status);} 
        const j=await r.json();
        renderChat(j.history||[]);
        loadMeta();
    }catch(e){
        if(box){box.innerHTML += `<div style='color:#ff7b72;font-size:.6rem'>Fehler: ${e}</div>`;}
    }
}
function clearChat(){const box=document.getElementById('chatBox');if(box)box.innerHTML='';}
document.addEventListener('keydown',e=>{
    const ta=document.getElementById('chatInput');
    if(!ta)return;
    if(e.key==='Enter' && !e.shiftKey && document.activeElement===ta){
        e.preventDefault();sendChat();
    }
});
async function streamChat(){
    const ta=document.getElementById('chatInput');if(!ta)return;const msg=ta.value.trim();if(!msg)return;ta.value='';
    const box=document.getElementById('chatBox');
    if(box){box.innerHTML+=`<div><span class='badge' style='background:#1f6feb'>user</span> ${escapeHtml(msg)}</div>`;}
    const es=new EventSource('/chat/stream?message='+encodeURIComponent(msg));
    let acc='';
    es.onmessage=(ev)=>{ // default event
        try{const data=JSON.parse(ev.data);if(data.delta){acc+=data.delta;}}
        catch{} 
        if(box){
            const rendered=renderMarkdown(acc);
            // replace last assistant preview or append
            const markerId='assistant-stream';
            let el=document.getElementById(markerId);
            if(!el){el=document.createElement('div');el.id=markerId;box.appendChild(el);} 
            el.innerHTML=`<span class='badge' style='background:#30363d'>assistant</span> ${rendered}`;
            box.scrollTop=box.scrollHeight;
        }
    };
    es.addEventListener('done',()=>{es.close();loadChat();loadMeta();});
    es.onerror=()=>{es.close();if(box){box.innerHTML+="<div style='color:#ff7b72'>[Stream Fehler]</div>";}};
}
async function chatToProposal(){
    try{
        const r=await fetch('/chat/to-proposal',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
        const j=await r.json();
        if(j.injected){
            loadPending();
        }else{
            alert('Chat→Proposal Fehler: '+(j.error||'?'));
        }
    }catch(e){alert('Chat→Proposal Netzwerkfehler: '+e);}
}
// Theme toggle
function applyTheme(t){document.documentElement.setAttribute('data-theme',t);localStorage.setItem('theme',t);document.getElementById('themeBtn').textContent=t==='light'?'🌙':'☀️';}
function toggleTheme(){const cur=document.documentElement.getAttribute('data-theme')==='light'?'dark':'light';applyTheme(cur);} 
applyTheme(localStorage.getItem('theme')||'dark');
// Diff modal
async function showDiff(id){
    try{const r=await fetch('/preview/'+id);const j=await r.json();renderDiff(id,j.diff||'');}catch(e){renderDiff(id,'Fehler beim Laden: '+e);} 
}
function renderDiff(id,raw){const m=document.getElementById('diffModal');m.classList.add('active');document.getElementById('diffTitle').textContent='Diff '+id;const body=document.getElementById('diffBody');body.innerHTML='';const lines=(raw||'').split(/\\r?\\n/);lines.forEach(l=>{if(!l) return;const div=document.createElement('div');div.className='diff-line'+(l.startsWith('+')?' add':(l.startsWith('-')?' del':''));div.textContent=l;body.appendChild(div);});window._lastDiff=raw;}
function closeDiff(){document.getElementById('diffModal').classList.remove('active');}
function copyDiff(){if(!window._lastDiff)return;navigator.clipboard.writeText(window._lastDiff);}
window.addEventListener('keydown',e=>{if(e.key==='Escape')closeDiff();});
loadAll();refreshSnapshots();listChanged();loadChat();
</script>
</body></html>
"""

@app.get("/")
async def index():
        return Response(content=INDEX_HTML, media_type="text/html")

@app.get("/meta", response_model=MetaResp)
async def meta():
    parse_meta = getattr(orch.evolution, 'last_parse_meta', {})
    changed = len(twin.diff_changed_files())
    snap_count = len(snaps.list())
    model = gclient.cfg.model
    return MetaResp(parse_meta=parse_meta, cycle=orch.state.cycle, pending=len(orch.list_pending()), twin_changed=changed, snapshots=snap_count, model=model, objectives=orch.state.objectives, chat_messages=len(_chat_history))

# ---- Objectives & Analysis & Injection ---- #
class ObjectivesUpdate(BaseModel):
    objectives: List[str]

@app.post("/objectives")
async def update_objectives(req: ObjectivesUpdate):
    orch.state.objectives = [o.strip() for o in req.objectives if o.strip()][:12]
    return {"objectives": orch.state.objectives}

class AnalysisSuggestion(BaseModel):
    id: str
    title: str
    rationale: str
    diff_hint: str | None = None

class AnalyzeResp(BaseModel):
    suggestions: List[AnalysisSuggestion]
    took_ms: float

class InjectResp(BaseModel):
    injected: str
    pending: int

@app.get("/analyze", response_model=AnalyzeResp)
async def analyze():
    start = time.time()
    if to_thread:
        suggestions = await to_thread.run_sync(lambda: analyze_repository(repo_root, orch.state.objectives, gclient))
    else:
        suggestions = analyze_repository(repo_root, orch.state.objectives, gclient)
    out: List[AnalysisSuggestion] = []
    for s in suggestions:
        try:
            out.append(AnalysisSuggestion(id=str(s.get('id','')), title=str(s.get('title',''))[:160], rationale=str(s.get('rationale',''))[:600], diff_hint=s.get('diff_hint')))
        except Exception:
            continue
    return AnalyzeResp(suggestions=out, took_ms=round((time.time()-start)*1000,1))

class InjectRequest(BaseModel):
    title: str
    rationale: str
    diff: str

@app.post("/inject-proposal", response_model=InjectResp)
async def inject_proposal(req: InjectRequest):
    from ..core.models import PatchProposal, Score
    pid = f"ux{int(time.time())}"
    proposal = PatchProposal(id=pid, title=req.title[:160], description=req.rationale[:400], diff=req.diff[:20000], rationale=req.rationale[:600], risk_note="injected")
    proposal.score = Score(clarity=0.6, impact=0.6, risk=0.4, effort=0.5)
    orch.approvals.submit(proposal)
    return InjectResp(injected=pid, pending=len(orch.list_pending()))

# ---- Chat Endpoints ---- #
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
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.strip())
        except Exception:
            return 0.0
    return 0.0

def _history_as_models() -> List[ChatMessage]:
    return [ChatMessage(role=str(h.get('role', 'user')),
                        content=str(h.get('content', '')),
                        ts=_coerce_ts(h.get('ts'))) for h in _chat_history]

def _build_chat_messages(user_text: str):
    from ..core.models import Message
    # include current objectives (truncated) in system prompt for better alignment
    objectives_txt = '\n'.join(f"- {o}" for o in orch.state.objectives[:8]) or '(keine gesetzt)'
    sys = ("You are a concise assistant helping evolve this repository.\n"
           "Objectives (user defined):\n" + objectives_txt + "\n"
           "When the user asks for improvements, propose concrete code changes. If suitable, you may outline a diff_hint (filename + intention). Respond brief.")
    msgs = [Message(role="system", content=sys)]
    for h in _chat_history[-10:]:
        role = str(h.get('role','user'))
        content = str(h.get('content',''))
        msgs.append(Message(role=role, content=content))
    msgs.append(Message(role="user", content=user_text))
    return msgs

@app.post("/chat", response_model=ChatReply)
async def chat(req: ChatRequest):
    text = req.message.strip()
    if not text:
        return ChatReply(reply="(leer)", history=_history_as_models())
    _chat_history.append({"role": "user", "content": text, "ts": time.time()})
    _save_chat_history()
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
