// Evolution Sandbox Chat UI
const $ = sel => document.querySelector(sel);
const api = {
  async postChat(message){
    const r = await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})});
    if(!r.ok) throw new Error('HTTP '+r.status);
    return r.json();
  },
  async history(){
    const r = await fetch('/chat/history');
    return r.json();
  },
  async meta(){
    const r = await fetch('/api'); return r.json();
  },
  async health(){
    const r = await fetch('/health'); return r.json();
  }
};

const messagesEl = $('#messages');
const inputEl = $('#chat-input');
const btnSend = $('#btn-send');
const btnKbSave = $('#btn-kb-save');
const pendingEl = $('#pending-indicator');
const chkStream = $('#chk-stream');
const helpPanel = $('#help-panel');
const helpContent = $('#help-content');
const suggBar = document.querySelector('#suggestions-bar');
const proposalsList = document.querySelector('#proposals-list');
const improveBox = document.querySelector('#improve-box');
const contextBar = document.querySelector('#context-sugg-bar');

async function fetchJSON(url, opts={}){
  const r = await fetch(url, opts); if(!r.ok) throw new Error(r.status+' '+url); return r.json();
}

async function refreshProposals(){
  if(!proposalsList) return;
  proposalsList.textContent='(lade...)';
  try {
    const data = await fetchJSON('/proposals/pending');
    if(!data.items.length){ proposalsList.textContent='(keine)'; return; }
    proposalsList.innerHTML='';
    data.items.forEach(p=>{
      const div=document.createElement('div');
      div.className='prop-item';
      const score = p.composite!=null? (' '+p.composite.toFixed(2)) : '';
      div.innerHTML = `<strong>${p.id}</strong> ${p.title.replace(/</g,'&lt;')}${score}`;
      const btnWrap=document.createElement('div');
      btnWrap.style.display='flex'; btnWrap.style.gap='4px'; btnWrap.style.flexWrap='wrap'; btnWrap.style.margin='4px 0 8px';
      const bPrev=document.createElement('button'); bPrev.textContent='Diff'; bPrev.addEventListener('click',()=>showProposalDiff(p.id, div));
      const bApply=document.createElement('button'); bApply.textContent='Apply'; bApply.addEventListener('click',()=>applyProposal(p.id));
      btnWrap.appendChild(bPrev); btnWrap.appendChild(bApply); div.appendChild(btnWrap);
      proposalsList.appendChild(div);
    });
  } catch(e){ proposalsList.textContent='Fehler: '+e.message; }
}

async function showProposalDiff(id, container){
  try {
    const data = await fetchJSON('/proposals/preview/'+id);
    let pre = container.querySelector('pre.diff');
    if(!pre){ pre=document.createElement('pre'); pre.className='diff'; container.appendChild(pre); }
    pre.textContent=data.diff.slice(0,4000);
  } catch(e){ addMessage('system','Diff Fehler '+e.message); }
}

async function applyProposal(id){
  pending(true);
  try {
    const data = await fetchJSON('/proposals/apply',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
    if(data.applied){ addMessage('assistant','Applied '+data.id+' -> '+(data.file||'')); }
    else { addMessage('system','Apply Fehler: '+data.error); }
    await refreshProposals();
  } catch(e){ addMessage('system','Apply Fehler '+e.message); }
  finally{ pending(false); }
}

async function undoLast(){
  pending(true);
  try {
    const data = await fetchJSON('/proposals/undo',{method:'POST'});
    if(data.undone){ addMessage('assistant','Undo '+data.id); }
    else { addMessage('system','Nichts zum Undo'); }
    await refreshProposals();
  } catch(e){ addMessage('system','Undo Fehler '+e.message); }
  finally{ pending(false); }
}

let SUGGESTIONS = [];

async function loadSuggestions(){
  try {
    const r = await fetch('/ui/suggestions');
    if(!r.ok) throw new Error('status '+r.status);
    const data = await r.json();
    SUGGESTIONS = data.items || [];
  } catch(e){
    // Fallback statisch
    SUGGESTIONS = [
      { label: 'Hilfe', cmd: '/help' },
      { label: 'Analyse', cmd: '/analyze' }
    ];
  }
  renderSuggestions();
}

async function loadContextSuggestions(){
  if(!contextBar) return;
  try{
    const r = await fetch('/ui/context-suggestions');
    if(!r.ok) throw new Error(r.status);
    const data = await r.json();
    contextBar.innerHTML='';
    data.items.forEach(s=>{
      const b=document.createElement('button');
      b.textContent=s.label; b.title=s.cmd + (s.hint? ('\n'+s.hint):'');
      b.addEventListener('click',()=>{ inputEl.value=s.cmd; sendCurrent(); });
      contextBar.appendChild(b);
    });
  }catch(e){ contextBar.textContent='(keine kontext vorschläge)'; }
}

function renderSuggestions(){
  if(!suggBar) return;
  suggBar.innerHTML='';
  SUGGESTIONS.forEach(s=>{
    const b=document.createElement('button');
    b.textContent=s.label;
    b.title=s.cmd + (s.hint? ('\n'+s.hint):'');
    b.addEventListener('click', (ev)=>{
      if(ev.shiftKey){
        inputEl.value = s.cmd; inputEl.focus();
      } else {
        inputEl.value = s.cmd; sendCurrent();
      }
    });
    suggBar.appendChild(b);
  });
}

function addMessage(role, content){
  const div = document.createElement('div');
  div.className = 'msg '+role;
  div.textContent = content;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

async function refreshHistory(){
  try {
    const data = await api.history();
    messagesEl.innerHTML='';
    (data.history||[]).forEach(m=> addMessage(m.role, m.content));
  } catch(e){ console.error(e); }
}

async function sendCurrent(){
  const text = inputEl.value.trim();
  if(!text) return; inputEl.value='';
  if(chkStream.checked){
    streamMessage(text);
    return;
  }
  pending(true);
  try {
    const data = await api.postChat(text);
    addMessage('user', text);
    addMessage('assistant', data.reply);
  } catch(e){
    addMessage('system', 'Fehler: '+e.message);
  } finally { pending(false); }
}

function pending(p){ pendingEl.hidden = !p; }

function streamMessage(text){
  addMessage('user', text);
  const url = '/chat/stream?message='+encodeURIComponent(text);
  const es = new EventSource(url);
  let acc='';
  const assistantDiv = document.createElement('div');
  assistantDiv.className='msg assistant';
  messagesEl.appendChild(assistantDiv);
  es.onmessage = ev => {
    try {
      const d = JSON.parse(ev.data);
      if(d.delta){ acc += d.delta; assistantDiv.textContent = acc; messagesEl.scrollTop = messagesEl.scrollHeight; }
    } catch(_){}
  };
  es.addEventListener('done', ()=> es.close());
  es.onerror = ()=> { es.close(); };
}

// Help & commands
$('#btn-help').addEventListener('click', async ()=>{
  pending(true);
  try {
    const data = await api.postChat('/help');
    helpContent.textContent = data.reply;
    helpPanel.classList.remove('hidden');
  } catch(e){ addMessage('system','Hilfe Fehler'); }
  finally { pending(false); }
});
$('#btn-close-help').addEventListener('click', ()=> helpPanel.classList.add('hidden'));

btnSend.addEventListener('click', sendCurrent);
inputEl.addEventListener('keydown', e=>{ if(e.key==='Enter' && !e.shiftKey){ sendCurrent(); }});

btnKbSave.addEventListener('click', async ()=>{
  pending(true);
  try { const data = await api.postChat('/kb.save'); addMessage('assistant', data.reply); }
  catch(e){ addMessage('system','KB Save Fehler'); }
  finally{ pending(false); }
});

$('#btn-refresh-status').addEventListener('click', async ()=>{
  pending(true);
  try { const data = await api.postChat('/meta'); addMessage('assistant', data.reply); }
  catch(e){ addMessage('system','Status Fehler'); }
  finally{ pending(false); }
});

$('#btn-objectives').addEventListener('click', async ()=>{
  pending(true);
  try { const data = await api.postChat('/objectives.list'); addMessage('assistant', data.reply); }
  catch(e){ addMessage('system','Objectives Fehler'); }
  finally{ pending(false); }
});

$('#btn-save-objectives').addEventListener('click', async ()=>{
  const raw = $('#objectives-edit').value.trim();
  if(!raw) return; pending(true);
  try { const data = await api.postChat('/objectives.set '+raw); addMessage('assistant', data.reply); }
  catch(e){ addMessage('system','Objectives Set Fehler'); }
  finally{ pending(false); }
});

$('#btn-knowledge').addEventListener('click', async ()=>{
  pending(true);
  try { const data = await api.postChat('/kb.list'); addMessage('assistant', data.reply); }
  catch(e){ addMessage('system','KB Fehler'); }
  finally{ pending(false); }
});

$('#btn-analysis').addEventListener('click', async ()=>{
  pending(true);
  try { const data = await api.postChat('/analyze'); addMessage('assistant', data.reply); }
  catch(e){ addMessage('system','Analyse Fehler'); }
  finally{ pending(false); }
});

document.querySelector('#btn-proposals-refresh')?.addEventListener('click', refreshProposals);
document.querySelector('#btn-proposal-undo')?.addEventListener('click', undoLast);
document.querySelector('#btn-improve-scan')?.addEventListener('click', async ()=>{
  pending(true);
  try{ const data = await api.postChat('/improve.scan'); addMessage('assistant', data.reply); improveBox && (improveBox.textContent=data.reply); await loadContextSuggestions(); }
  catch(e){ addMessage('system','Improve Fehler'); }
  finally{ pending(false); }
});

async function refreshImproveList(){
  if(!improveBox) return;
  try{
    const r = await fetch('/improve/json');
    if(!r.ok) throw new Error(r.status);
    const data = await r.json();
    if(!data.items.length){ improveBox.textContent='(leer)'; return; }
    improveBox.innerHTML='';
    data.items.forEach(s=>{
      const d=document.createElement('div');
      d.className='imp-item';
      d.innerHTML = `<strong>${s.id}</strong> ${ (s.title||'').replace(/</g,'&lt;') }`;
      const btn=document.createElement('button'); btn.textContent='Inject'; btn.addEventListener('click',()=>injectImprove(s.id)); btn.style.marginLeft='6px';
      d.appendChild(btn);
      improveBox.appendChild(d);
    });
  }catch(e){ improveBox.textContent='Fehler: '+e.message; }
}

async function injectImprove(id){
  pending(true);
  try{
    const r = await fetch('/improve/inject',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({id})});
    const data = await r.json();
    if(data.injected){ addMessage('assistant','Improve injected '+data.proposal_id); await refreshProposals(); }
    else { addMessage('system','Inject Fehler: '+data.error); }
  }catch(e){ addMessage('system','Inject Fehler '+e.message); }
  finally{ pending(false); }
}

async function init(){
  await refreshHistory();
  addMessage('system','UI geladen.');
  await loadSuggestions();
  await loadContextSuggestions();
  await refreshProposals();
  await refreshImproveList();
  inputEl.focus();
}

init();
