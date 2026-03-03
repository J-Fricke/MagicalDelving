# src/magicaldelving/mulligan_sim/rules/keywords.py
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

# Canonical keyword strings used in Permanent.keywords
HASTE = "Haste"
TRAMPLE = "Trample"
FLYING = "Flying"
MENACE = "Menace"
VIGILANCE = "Vigilance"
FIRST_STRIKE = "FirstStrike"
DOUBLE_STRIKE = "DoubleStrike"
DEATHTOUCH = "Deathtouch"
LIFELINK = "Lifelink"
INDESTRUCTIBLE = "Indestructible"
HEXPROOF = "Hexproof"
REACH = "Reach"

ALL: FrozenSet[str] = frozenset({
    HASTE, TRAMPLE, FLYING, MENACE, VIGILANCE, FIRST_STRIKE, DOUBLE_STRIKE,
    DEATHTOUCH, LIFELINK, INDESTRUCTIBLE, HEXPROOF, REACH,
})

def has(p, kw: str) -> bool:
    return kw in getattr(p, "keywords", set())

def has_haste(p, st) -> bool:
    # Finisher haste is still a global knob; later replace with continuous effects.
    return bool(getattr(st, "finisher_haste", False) or has(p, HASTE))

def has_vigilance(p) -> bool:
    return has(p, VIGILANCE)

def has_double_strike(p) -> bool:
    return has(p, DOUBLE_STRIKE)

def has_first_strike(p) -> bool:
    return has(p, FIRST_STRIKE) or has(p, DOUBLE_STRIKE)
