from __future__ import annotations

from typing import List, Optional, Tuple

from .index import CardIndex
from .models import GameState, Permanent


def global_haste_online(st: GameState, idx: CardIndex) -> bool:
    for p in st.iter_permanents():
        txt = (idx.oracle_for_perm(p) or "").lower()
        if "have haste" in txt and ("creatures you control" in txt or "creatures have haste" in txt):
            return True
    return False


def is_hasty_creature(p: Permanent, idx: CardIndex) -> bool:
    # treat as creature if either rules say so OR deck logic marked it so this turn
    if not (idx.is_creature_perm(p) or p.is_creature_this_turn):
        return False
    return "haste" in (idx.oracle_for_perm(p) or "").lower()


def can_use_creature_this_turn(st: GameState, idx: CardIndex, p: Permanent) -> bool:
    """Eligibility for tap/attack this turn (summoning sickness + haste)."""
    if not (idx.is_creature_perm(p) or p.is_creature_this_turn):
        return False

    haste_all = global_haste_online(st, idx) or st.finisher_haste
    if p.entered_turn >= st.turn:
        return bool(haste_all or is_hasty_creature(p, idx))
    return True


def attackable_creature_powers(st: GameState, idx: CardIndex) -> List[int]:
    powers: List[int] = []
    for p in st.iter_permanents():
        if not (idx.is_creature_perm(p) or p.is_creature_this_turn):
            continue
        if p.tapped:
            continue
        if not can_use_creature_this_turn(st, idx, p):
            continue

        if p.attack_power_override_this_turn is not None:
            powers.append(max(0, int(p.attack_power_override_this_turn)))
            continue

        f = idx.facts(p.name)
        powers.append(max(0, int(f.power)) if (f and f.power is not None) else 2)

    return powers


def attackable_tokens(st: GameState, idx: CardIndex) -> int:
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    return st.token_pool if haste_all else max(0, st.token_pool - st.tokens_created_this_turn)


def max_creature_power(st: GameState, idx: CardIndex) -> int:
    mx = 1 if st.token_pool > 0 else 0
    for p in st.iter_permanents():
        if not (idx.is_creature_perm(p) or p.is_creature_this_turn):
            continue

        if p.attack_power_override_this_turn is not None:
            mx = max(mx, int(p.attack_power_override_this_turn))
            continue

        f = idx.facts(p.name)
        mx = max(mx, int(f.power) if (f and f.power is not None) else 2)
    return mx


def evaluate_damage_this_turn(st: GameState, idx: CardIndex) -> int:
    def battlefield_has(role: str) -> bool:
        return any(role in idx.roles_for_perm(p) for p in st.iter_permanents())

    powers = attackable_creature_powers(st, idx)
    creature_power = sum(powers)

    tok = max(0, attackable_tokens(st, idx) - st.tokens_tapped_for_mana)
    attackers = len(powers) + tok
    if attackers <= 0:
        return 0

    nominal = creature_power + tok

    ascension_on_board = any((p.name or "").strip().lower() == "beastmaster ascension" for p in st.iter_permanents())
    ascension_active = ascension_on_board and attackers >= 7
    boost = st.finisher_boost + (5 if ascension_active else 0)
    trample = st.finisher_trample or ascension_active

    if boost > 0:
        nominal += boost * attackers

    if battlefield_has("Evasion"):
        connect = 0.85
    elif trample:
        connect = 0.75
    else:
        connect = 0.60

    through = int(nominal * connect)

    extra = sum(1 for p in st.iter_permanents() if "ExtraCombat" in idx.roles_for_perm(p))
    if extra:
        through += int(through * 0.70 * extra)

    return max(0, through)


def tablekill_step(
        st: GameState,
        idx: CardIndex,
        combat_total: int,
        *,
        win_by_turn: int = 8,
        threshold: int = 120,
        turn_hit_120: Optional[int] = None,
) -> Tuple[int, bool, Optional[int]]:
    """
    Call once per turn AFTER you update st for that turn.
    Returns: (new_combat_total, tablekill_by_win_by_turn, turn_hit_120)
    """
    combat_total += evaluate_damage_this_turn(st, idx)

    if combat_total >= threshold and turn_hit_120 is None:
        turn_hit_120 = st.turn

    tablekill_by_target_turn = (turn_hit_120 is not None) and (turn_hit_120 <= win_by_turn)
    return combat_total, tablekill_by_target_turn, turn_hit_120