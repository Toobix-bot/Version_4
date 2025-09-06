"""Variant decision marker.

This module centralizes high-level variant configuration chosen by the user.

Decisions (Variante: go):
 - Feature-Kategorien aktiv: System, Ziele, Analyse, World, Improve, Knowledge
 - Analyse Limits: max 3s Laufzeit, max 100 Dateien (Sampling Stop)
 - Welt Startgröße: 40x24
 - Ressourcenmodell pro Entity: energy, knowledge, material
 - Autonomiegrenze: KI darf nur Regel-VORSCHLÄGE liefern (no auto rule mutation)
 - Zielmetriken (v1 Platzhalter): a, b, c, d
"""

from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class VariantConfig:
    enabled_categories: tuple[str, ...] = ("System","Ziele","Analyse","World","Improve","Knowledge")
    analysis_max_seconds: float = 3.0
    analysis_max_files: int = 100
    world_default_w: int = 40
    world_default_h: int = 24
    resources: tuple[str, ...] = ("energy","knowledge","material")
    autonomy_mode: str = "suggest-only"  # future: 'limited', 'full'
    goal_metrics: tuple[str, ...] = ("a","b","c","d")

VARIANT = VariantConfig()
