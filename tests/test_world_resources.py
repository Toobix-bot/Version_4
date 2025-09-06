import time
from pathlib import Path
from src.sim import world

def setup_module(module):
    # ensure clean state
    world.STATE['w']=0; world.STATE['h']=0; world.STATE['entities']=[]; world.STATE['ticks']=0

def test_world_resource_growth():
    msg = world.init_world(10,6)
    assert 'World init' in msg
    world.spawn('agent')
    start_ticks = world.STATE['ticks']
    world.tick(15)
    assert world.STATE['ticks'] >= start_ticks + 15
    ents = world.STATE['entities']
    assert ents, 'no entities spawned'
    e = ents[0]
    # after ticks some exp or resource should have grown
    assert e.get('exp',0) > 0 or e.get('knowledge',0) > 0 or e.get('material',0) > 0
    # run more ticks to exercise interaction path (even if single entity)
    world.tick(5)
    # Nothing should exceed defined caps
    assert e.get('energy',0) <= 1.0
    assert e.get('knowledge',0) <= 10.0
    assert e.get('material',0) <= 5.0
