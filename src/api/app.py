from __future__ import annotations
from fastapi import FastAPI, Response
from pydantic import BaseModel
from pathlib import Path
from typing import List
from ..core.orchestrator import Orchestrator
from ..io.groq_client import GroqClient

app = FastAPI(title="Evolution Sandbox API", version="0.1.0")
orch = Orchestrator(repo_root=Path(__file__).resolve().parent.parent.parent)
gclient = GroqClient()

class Proposal(BaseModel):
    id: str
    title: str
    description: str
    score: float | None

class CycleResponse(BaseModel):
    cycle: int
    proposals: List[Proposal]

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

@app.get("/health")
async def health():
        return {"status": "ok", "cycle": orch.state.cycle, "pending": len(orch.list_pending())}

@app.get("/groq-check")
async def groq_check():
    if not gclient.cfg.api_key:
        return {"api_key": False, "mode": "no-key", "message": "Kein API_KEY gesetzt."}
    # very small test prompt
    from ..core.models import Message
    msg = [Message(role="user", content="Sag nur OK.")]
    out = gclient.chat_completion(msg)
    truncated = out.strip()[:200]
    return {"api_key": True, "response_sample": truncated}

INDEX_HTML = """
<!doctype html>
<html lang='de'>
<head><meta charset='utf-8'/><title>Evolution Sandbox</title>
<style>
body{font-family:system-ui,Arial,sans-serif;margin:2rem;max-width:900px}
code{background:#eee;padding:2px 4px;border-radius:4px}
table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 6px}
button{cursor:pointer;padding:.4rem .8rem;margin:.2rem}
.id{font-family:monospace;color:#555}
</style></head>
<body>
<h1>Evolution Sandbox</h1>
<div id='status'></div>
<div>
    <button onclick='runCycle()'>Cycle</button>
    <button onclick='loadPending()'>Pending</button>
    <button onclick='undoLast()'>Undo</button>
</div>
<h2>Neue Vorschläge</h2>
<ul id='new'></ul>
<h2>Offen</h2>
<ul id='pending'></ul>
<script>
async function runCycle(){
    const r=await fetch('/cycle',{method:'POST'});const j=await r.json();
    document.getElementById('status').textContent='Cycle '+j.cycle;
    const ul=document.getElementById('new');ul.innerHTML='';
    j.proposals.forEach(p=>{const li=document.createElement('li');li.innerHTML=`<span class='id'>${p.id}</span> <b>${p.title}</b> (Score: ${p.score??'?'}) <button onclick="applyOne('${p.id}')">Apply</button>`;ul.appendChild(li);});
    loadPending();
}
async function loadPending(){
    const r=await fetch('/pending');const j=await r.json();
    const ul=document.getElementById('pending');ul.innerHTML='';
    j.forEach(p=>{const li=document.createElement('li');li.innerHTML=`<span class='id'>${p.id}</span> <b>${p.title}</b> (Score ${p.score??'?'}) <button onclick="applyOne('${p.id}')">Apply</button>`;ul.appendChild(li);});
    const h=await fetch('/health');const hj=await h.json();document.getElementById('status').textContent=`Cycle ${hj.cycle} | Pending ${hj.pending}`;
}
async function applyOne(id){
    await fetch('/apply/'+id,{method:'POST'});loadPending();
}
async function undoLast(){
    await fetch('/undo',{method:'POST'});loadPending();
}
loadPending();
</script>
</body></html>
"""

@app.get("/")
async def index():
        return Response(content=INDEX_HTML, media_type="text/html")
