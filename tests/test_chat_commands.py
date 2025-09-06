import os
from fastapi.testclient import TestClient

# disable rate limiting before import
os.environ['DISABLE_RATE_LIMIT'] = '1'
os.environ['RATE_LIMIT_PER_MIN'] = '0'
from src.api import app as app_module  # type: ignore
app_module.RATE_LIMIT_PER_MIN = 0  # type: ignore
app = app_module.app
client = TestClient(app)

ZERO_WIDTH = '\u200b'

def post(msg: str):
    r = client.post('/chat', json={'message': msg})
    assert r.status_code == 200, r.text
    return r.json()['reply']

def test_help_command_basic():
    rep = post('/help')
    assert 'Befehle (Kategorien):' in rep


def test_zero_width_prefix():
    rep = post(ZERO_WIDTH + '/help')
    assert 'Befehle (Kategorien):' in rep


def test_unknown_command():
    rep = post('/unknownzzz')
    assert 'Unbekannt:' in rep
