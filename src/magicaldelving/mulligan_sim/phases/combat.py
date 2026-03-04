from __future__ import annotations

from typing import Optional, Tuple

from ..index import CardIndex
from ..models import GameState
from ..engine.continuous import ensure_continuous_effects
from ..rules import keywords as kw


def evaluate_combat_step(st: GameState, idx: CardIndex) -> int:
    st.audit_phase = "COMBAT"
    attackers = []
    attackers_qty = 0

    for p in st.iter_permanents():
        if p.can_attack(st):
            attackers.append(p)
            attackers_qty += p.qty

    st.attackers_this_turn = attackers_qty
    if attackers_qty <= 0:
        return 0

    # Attack triggers may create tokens / buffs / etc.
    st.audit("DECLARE_ATTACKERS", attackers=[{"pid": p.pid, "name": p.name, "qty": p.qty, "power": p.power_int(), "keywords": sorted(p.keywords)} for p in attackers])
    st.emit("ATTACK", {"attackers": attackers})
    ensure_continuous_effects(st, idx)

    total = 0
    for p in attackers:
        dmg = p.power_int() * p.qty
        if kw.has_double_strike(p):
            dmg *= 2
        total += dmg

    total = int(total * 0.60)
    st.audit("COMBAT_DAMAGE", damage=total, connect=0.60)
    return max(0, total)


# Back-compat if anything still imports it:
def evaluate_damage_this_turn(st: GameState, idx: CardIndex) -> int:
    return evaluate_combat_step(st, idx)


def tablekill_step(
        st: GameState,
        idx: CardIndex,
        combat_total: int,
        *,
        win_by_turn: int = 8,
        threshold: int = 120,
        turn_hit_120: Optional[int] = None,
) -> Tuple[int, bool, Optional[int]]:
    combat_total += evaluate_combat_step(st, idx)

    if combat_total >= threshold and turn_hit_120 is None:
        turn_hit_120 = st.turn

    ok = (turn_hit_120 is not None) and (turn_hit_120 <= win_by_turn)
    return combat_total, ok, turn_hit_120
