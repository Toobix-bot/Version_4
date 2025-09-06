from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Optional
import random, time, json

# Simple 2D world simulation (lightweight, no external deps)

@dataclass
class Entity:
    id: str
    kind: str
    x: int
    y: int
    energy: float = 1.0
    knowledge: float = 0.0
    material: float = 0.0
    exp: float = 0.0
    brain: str = "auto"  # auto|user
    notes: List[str] = None  # type: ignore

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d.get('notes') is None:
            d['notes'] = []
        return d

STATE: Dict[str, Any] = {
    'w': 0,
    'h': 0,
    'entities': [],  # list[dict]
    'ticks': 0,
    'controlled': None,  # entity id user controls
    'variant': 'go',  # marker
}

_world_path: Optional[Path] = None

# --- Persistence ---

def configure_persistence(base: Path):
    global _world_path
    _world_path = base / 'world.json'
    if _world_path.exists():
        try:
            raw = json.loads(_world_path.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                for k in ('w','h','entities','ticks','controlled'):
                    if k in raw:
                        STATE[k] = raw[k]
        except Exception:
            pass


def _save():  # best effort
    if not _world_path:
        return
    try:
        _world_path.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass

# --- Core Ops ---

def init_world(w: int, h: int) -> str:
    w = max(4, min(200, int(w)))
    h = max(4, min(200, int(h)))
    STATE['w'] = w; STATE['h'] = h
    STATE['entities'] = []
    STATE['ticks'] = 0
    STATE['controlled'] = None
    _save()
    return f"World init {w}x{h}."


def spawn(kind: str, brain: str = 'auto') -> str:
    if STATE['w'] <= 0:
        return 'World nicht initialisiert (/world.init w h).'
    eid = f"e{int(time.time()*1000)%1_000_000}"
    x = random.randint(0, STATE['w']-1)
    y = random.randint(0, STATE['h']-1)
    ent = Entity(id=eid, kind=kind[:16], x=x, y=y, brain=('user' if brain=='user' else 'auto'), notes=[])
    STATE['entities'].append(ent.to_dict())
    _save()
    return f"Spawned {eid} ({kind})."


def control(eid: str) -> str:
    for e in STATE['entities']:
        if e['id'] == eid:
            STATE['controlled'] = eid; _save(); return f"Kontrolliert jetzt: {eid}"
    return 'Entity nicht gefunden.'


def move(dx: int, dy: int) -> str:
    eid = STATE.get('controlled')
    if not eid:
        return 'Kein Entity kontrolliert. /world.control <id>'
    for e in STATE['entities']:
        if e['id'] == eid:
            e['x'] = max(0, min(STATE['w']-1, e['x'] + dx))
            e['y'] = max(0, min(STATE['h']-1, e['y'] + dy))
            e['energy'] = max(0.0, e.get('energy',1.0)-0.02)
            e['exp'] = e.get('exp',0.0) + 0.01
            _save()
            return f"Move {eid} -> ({e['x']},{e['y']})"
    return 'Entity nicht gefunden.'


def tick(n: int = 1) -> str:
    if STATE['w'] <= 0:
        return 'World nicht initialisiert.'
    n = max(1, min(200, int(n)))
    for _ in range(n):
        STATE['ticks'] += 1
        # 1. autonomous movement & passive gains
        for e in STATE['entities']:
            if e.get('brain') == 'auto' and e.get('energy', 0) > 0.05:
                if random.random() < 0.6:
                    e['x'] = max(0, min(STATE['w'] - 1, e['x'] + random.choice([-1, 0, 1])))
                    e['y'] = max(0, min(STATE['h'] - 1, e['y'] + random.choice([-1, 0, 1])))
                    e['energy'] = max(0.0, e.get('energy', 1.0) - 0.01)
                    e['exp'] = e.get('exp', 0.0) + 0.005
                    if random.random() < 0.2:
                        e['knowledge'] = min(10.0, e.get('knowledge', 0.0) + 0.01)
                    if random.random() < 0.15:
                        e['material'] = min(5.0, e.get('material', 0.0) + 0.005)
        # 2. interaction bonuses for shared cells
        cell_map: Dict[tuple[int, int], int] = {}
        for e in STATE['entities']:
            pos = (e['x'], e['y'])
            cell_map[pos] = cell_map.get(pos, 0) + 1
        for e in STATE['entities']:
            if cell_map.get((e['x'], e['y']), 0) > 1:
                e['exp'] = e.get('exp', 0.0) + 0.002
                e['energy'] = min(1.0, e.get('energy', 1.0) + 0.003)
                e['knowledge'] = min(10.0, e.get('knowledge', 0.0) + 0.004)
                e['material'] = min(5.0, e.get('material', 0.0) + 0.001)
    _save()
    return f"Tick {n} -> T={STATE['ticks']}"


def entities_summary(limit: int = 30) -> str:
    ents = STATE['entities'][:limit]
    if not ents:
        return '(leer)'
    return ' '.join(
        f"{e['id']}:{e['kind']}@{e['x']},{e['y']} E={e.get('energy',0):.2f} K={e.get('knowledge',0):.2f} M={e.get('material',0):.2f} X={e.get('exp',0):.2f}" 
        for e in ents)


def world_info() -> str:
    return (f"World {STATE['w']}x{STATE['h']} ticks={STATE['ticks']} ents={len(STATE['entities'])} "
            f"ctrl={STATE.get('controlled') or '-'} variant={STATE.get('variant')}")

