# src/magicaldelving/mulligan_sim/rules/types.py
from __future__ import annotations

from typing import FrozenSet

CREATURE = "Creature"
ARTIFACT = "Artifact"
ENCHANTMENT = "Enchantment"
LAND = "Land"
PLANESWALKER = "Planeswalker"
BATTLE = "Battle"
INSTANT = "Instant"
SORCERY = "Sorcery"

ALL: FrozenSet[str] = frozenset({
    CREATURE, ARTIFACT, ENCHANTMENT, LAND, PLANESWALKER, BATTLE, INSTANT, SORCERY
})

def is_type(p, t: str) -> bool:
    return t in getattr(p, "types", set())
