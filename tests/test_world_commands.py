import os, json
from fastapi.testclient import TestClient

# disable rate limiting for this module BEFORE app import
os.environ['RATE_LIMIT_PER_MIN'] = '0'
os.environ['DISABLE_RATE_LIMIT'] = '1'
from src.api import app as app_module  # type: ignore
app_module.RATE_LIMIT_PER_MIN = 0  # type: ignore
app = app_module.app
client = TestClient(app)

def chat(msg: str):
    r = client.post('/chat', json={'message': msg})
    assert r.status_code == 200, r.text
    return r.json()['reply']

def test_world_flow():
    # init
    rep = chat('/world.init 12 8')
    assert 'World init' in rep
    # spawn
    rep = chat('/world.spawn agent')
    assert 'Spawned' in rep
    # info
    rep = chat('/world.info')
    assert 'World 12x8' in rep
    # ents
    rep = chat('/world.ents')
    assert 'e' in rep or rep=='(leer)'
    # tick
    rep = chat('/world.tick 5')
    assert 'Tick' in rep
