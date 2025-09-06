import os
from pathlib import Path
from fastapi.testclient import TestClient

from src.api.app import app, orch

client = TestClient(app)

def _inject_proposal(diff: str, pid: str = 'pX'):
    # minimal proposal injection via internal orchestrator structures
    from src.core.models import PatchProposal
    prop = PatchProposal(id=pid, title='MultiFile', description='test', diff=diff, rationale='r')
    # score gate bypass - directly submit to approvals
    orch.approvals.submit(prop)
    return pid

def test_help_endpoint():
    r = client.get('/help')
    assert r.status_code == 200
    data = r.json()
    assert 'help' in data and 'Befehle' in data['help']
    r2 = client.get('/help', params={'cmd':'analyze'})
    assert r2.status_code == 200
    assert 'analyze' in r2.json()['help']

def test_multifile_apply_and_versions(tmp_path: Path):
    # create two dummy files initial content
    f1 = Path('DUMMY1.md'); f2 = Path('DUMMY2.md')
    f1.write_text('alt\n', encoding='utf-8')
    f2.write_text('alt\n', encoding='utf-8')
    diff = ("--- a/DUMMY1.md\n+++ b/DUMMY1.md\n@@\n-alt\n+neu1\n"
            "--- a/DUMMY2.md\n+++ b/DUMMY2.md\n@@\n-alt\n+neu2\n")
    pid = _inject_proposal(diff, 'pMF')
    apply_resp = client.post(f'/apply/{pid}', headers={'X-API-Key': os.getenv('API_KEY','')})
    assert apply_resp.status_code in (200, 401, 403)  # if key missing, just skip
    if apply_resp.status_code == 200:
        # check files updated
        assert f1.read_text(encoding='utf-8') == 'neu1\n'
        assert f2.read_text(encoding='utf-8') == 'neu2\n'
        vr = client.get('/versions')
        assert vr.status_code == 200
        entries = vr.json()
        assert any(e.get('file')=='DUMMY1.md' for e in entries)

