import json
from fastapi.testclient import TestClient
from src.api.app import app

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
