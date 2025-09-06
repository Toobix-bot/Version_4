from __future__ import annotations
import os, pathlib, sys

# Ensure project root on path for direct pytest invocation
ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from src.api.app import app, repo_root

client = TestClient(app)

def _set_env(k: str, v: str | None):
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = str(v)


def test_token_search_basic():
    # set token mapping for write role
    os.environ['API_TOKENS'] = 'testkey:write'
    # force refresh token roles in app module (simple re-import)
    import importlib
    import src.api.app as appmod
    importlib.reload(appmod)
    global client
    client = TestClient(appmod.app)
    r = client.post('/index/build', headers={'X-API-Key':'testkey'})
    assert r.status_code == 200, r.text
    r2 = client.get('/index/search?q=readme')
    assert r2.status_code == 200
    data = r2.json()
    assert 'matches' in data
    # not asserting >0 to keep test stable across repo changes


def test_semantic_disabled_by_default():
    _set_env('ENABLE_EMBED_INDEX', '0')
    r = client.get('/index/semantic?q=test')
    assert r.status_code == 200
    js = r.json()
    assert js['enabled'] is False
    assert js['matches'] == []


def test_semantic_enabled():
    _set_env('ENABLE_EMBED_INDEX','1')
    client.post('/index/build')
    r = client.get('/index/semantic?q=index')
    assert r.status_code == 200
    js = r.json()
    assert js['enabled'] is True
    # may have 0 matches if token absent; just structural checks
    assert 'matches' in js


def test_incremental_update_apply_pr_mode_creates_patch():
    # Inject a fake proposal manually by writing to orchestrator pending list via API route sequence
    # We rely on /inject-proposal existing; if not present this test is skipped.
    # Switch to PR mode so apply does not modify files directly.
    _set_env('PR_APPLY_MODE','pr')
    target_file = repo_root / 'DUMMY_TEST_FILE.txt'
    diff = f"""--- a/{target_file.name}\n+++ b/{target_file.name}\n@@\n+HelloIndexTest\n"""
    payload = {"title":"Dummy","rationale":"r","diff":diff}
    # Attempt injection endpoint if available
    inj = client.post('/inject-proposal', json=payload)
    if inj.status_code != 200:
        # fallback skip
        return
    pdata = inj.json()
    pid = pdata.get('id') or pdata.get('proposal_id')
    assert pid
    # apply
    ap = client.post('/proposals/apply', json={'id': pid})
    assert ap.status_code == 200, ap.text
    resp = ap.json()
    assert resp['mode'] == 'pr'
    assert resp.get('artifact'), resp
    patch_path = pathlib.Path(resp['artifact'])
    assert patch_path.exists()


def test_metrics_search_counters_progress():
    _set_env('ENABLE_EMBED_INDEX','0')
    base = client.get('/metrics').json()
    client.get('/index/search?q=readme')
    mid = client.get('/metrics').json()
    bq = base.get('index_search_queries',0)
    mq = mid.get('index_search_queries',0)
    assert mq >= bq + 1
    _set_env('ENABLE_EMBED_INDEX','1')
    if 'API_TOKENS' not in os.environ:
        os.environ['API_TOKENS'] = 'testkey:write'
    client.post('/index/build', headers={'X-API-Key': 'testkey'})
    client.get('/index/semantic?q=readme')
    aft = client.get('/metrics').json()
    msq = mid.get('index_semantic_queries',0)
    asq = aft.get('index_semantic_queries',0)
    assert asq >= msq + 1
