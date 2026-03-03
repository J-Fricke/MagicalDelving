# src/magicaldelving/mulligan_sim/rules/eligibility.py
from __future__ import annotations

from ..models import GameState, Permanent
from . import keywords as kw
from . import types as ty


def is_creature(p: Permanent) -> bool:
    # Current truth only (temporary type changes should mutate p.types and be reverted via delayed cleanup)
    return ty.is_type(p, ty.CREATURE)


def can_tap(p: Permanent, st: GameState) -> bool:
    if not is_creature(p):
        return False
    if not p.sick:
        return True
    return kw.has_haste(p, st)


def can_attack(p: Permanent, st: GameState) -> bool:
    if not is_creature(p):
        return False
    if p.tapped:
        return False
    if not p.sick:
        return True
    return kw.has_haste(p, st)
