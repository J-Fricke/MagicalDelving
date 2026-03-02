from __future__ import annotations

from typing import List

from .index import CardIndex
from .models import GameState


def global_haste_online(st: GameState, idx: CardIndex) -> bool:
    for c in st.battlefield:
        f = idx.facts(c)
        if not f:
            continue
        txt = (f.oracle_text or "").lower()
        if "have haste" in txt and ("creatures you control" in txt or "creatures have haste" in txt):
            return True
    return False


def is_hasty_creature(name: str, idx: CardIndex) -> bool:
    f = idx.facts(name)
    if not f or not f.is_creature:
        return False
    return "haste" in (f.oracle_text or "").lower()


def attackable_creature_powers(st: GameState, idx: CardIndex) -> List[int]:
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    powers: List[int] = []
    for c in st.battlefield:
        f = idx.facts(c)
        if not (f and f.is_creature):
            continue

        entered = st.entered_turn.get(c, 0)
        if entered >= st.turn:
            if not haste_all and not is_hasty_creature(c, idx):
                continue

        powers.append(max(0, int(f.power)) if f.power is not None else 2)
    return powers


def attackable_tokens(st: GameState, idx: CardIndex) -> int:
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    return st.token_pool if haste_all else max(0, st.token_pool - st.tokens_created_this_turn)


def max_creature_power(st: GameState, idx: CardIndex) -> int:
    mx = 1 if st.token_pool > 0 else 0
    for c in st.battlefield:
        f = idx.facts(c)
        if not (f and f.is_creature):
            continue
        mx = max(mx, int(f.power) if f.power is not None else 2)
    return mx


def evaluate_damage_this_turn(st: GameState, idx: CardIndex) -> int:
    def battlefield_has(role: str) -> bool:
        return any(role in idx.roles(c) for c in st.battlefield)

    powers = attackable_creature_powers(st, idx)
    powers.sort()

    tapped = min(st.creatures_tapped_for_mana, len(powers))
    if tapped > 0:
        powers = powers[tapped:]

    creature_power = sum(powers)

    tok = max(0, attackable_tokens(st, idx) - st.tokens_tapped_for_mana)
    attackers = len(powers) + tok
    if attackers <= 0:
        return 0

    nominal = creature_power + tok

    ascension_on_board = any((c or "").strip().lower() == "beastmaster ascension" for c in st.battlefield)
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

    extra = sum(1 for c in st.battlefield if "ExtraCombat" in idx.roles(c))
    if extra:
        through += int(through * 0.70 * extra)

    return max(0, through)