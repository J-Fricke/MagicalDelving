from __future__ import annotations

import re
from collections import Counter
from typing import List, Optional, Tuple

from .index import CardIndex
from .models import GameState, Permanent


_CREW_RE = re.compile(r"\bcrew\s+(\d+)\b", re.IGNORECASE)


def global_haste_online(st: GameState, idx: CardIndex) -> bool:
    for p in st.iter_permanents():
        txt = (idx.oracle_for_perm(p) or "").lower()
        if "have haste" in txt and ("creatures you control" in txt or "creatures have haste" in txt):
            return True
    return False


def is_hasty_creature(p: Permanent, idx: CardIndex) -> bool:
    # treat as creature if either rules say so OR deck logic marked it so this turn
    if not (idx.is_creature_perm(p) or getattr(p, "is_creature_this_turn", False)):
        return False
    return "haste" in (idx.oracle_for_perm(p) or "").lower()


def can_use_creature_this_turn(st: GameState, idx: CardIndex, p: Permanent) -> bool:
    """Eligibility for tap/attack this turn (summoning sickness + haste)."""
    if not (idx.is_creature_perm(p) or getattr(p, "is_creature_this_turn", False)):
        return False

    haste_all = global_haste_online(st, idx) or st.finisher_haste
    if p.entered_turn >= st.turn:
        return bool(haste_all or is_hasty_creature(p, idx))
    return True


def _perm_power(p: Permanent, idx: CardIndex, *, default: int) -> int:
    override = getattr(p, "attack_power_override_this_turn", None)
    if override is not None:
        return max(0, int(override))

    f = idx.facts(p.name)
    if f and getattr(f, "power", None) is not None:
        try:
            return max(0, int(f.power))
        except Exception:
            pass
    return default


def _is_vehicle_perm(p: Permanent, idx: CardIndex) -> bool:
    f = idx.facts(p.name)
    tl = (getattr(f, "type_line", "") or "").lower() if f else ""
    if "vehicle" in tl:
        return True
    # fallback: if it has a crew ability in oracle text, it's almost certainly a Vehicle
    return bool(_CREW_RE.search((idx.oracle_for_perm(p) or "")))


def _crew_requirement(p: Permanent, idx: CardIndex) -> Optional[int]:
    m = _CREW_RE.search((idx.oracle_for_perm(p) or ""))
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _can_attack_if_crewed_this_turn(st: GameState, idx: CardIndex, p: Permanent) -> bool:
    """
    Vehicles aren't creatures by default, but once crewed they *are* creatures, so apply the same
    summoning-sickness rule: if it entered this turn, require haste (global haste / finisher haste / self-haste text).
    """
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    if p.entered_turn >= st.turn:
        txt = (idx.oracle_for_perm(p) or "").lower()
        has_self_haste = "haste" in txt
        return bool(haste_all or has_self_haste)
    return True


def _non_token_attack_stats_with_vehicle_crew(st: GameState, idx: CardIndex) -> Tuple[int, int]:
    """
    Returns (non_token_attackers, non_token_power_sum) for this turn, after optionally crewing Vehicles
    using untapped, eligible creature-permanents that would otherwise attack.

    Deck-agnostic: does not reference any specific card names; relies on type_line/oracle for Vehicles + Crew N.
    """
    # Baseline creature-like attackers (creatures + "is_creature_this_turn" projections).
    candidate_powers: List[int] = []
    for p in st.iter_permanents():
        if not (idx.is_creature_perm(p) or getattr(p, "is_creature_this_turn", False)):
            continue
        if p.tapped:
            continue
        if not can_use_creature_this_turn(st, idx, p):
            continue
        candidate_powers.append(_perm_power(p, idx, default=2))

    # Vehicles eligible to be crewed into attackers (not already creatures this turn).
    vehicles: List[Tuple[int, int]] = []  # (vehicle_power, crew_req)
    for v in st.iter_permanents():
        if v.tapped:
            continue
        if not _is_vehicle_perm(v, idx):
            continue
        if idx.is_creature_perm(v) or getattr(v, "is_creature_this_turn", False):
            continue

        crew_req = _crew_requirement(v, idx)
        if not crew_req or crew_req <= 0:
            continue
        if not _can_attack_if_crewed_this_turn(st, idx, v):
            continue

        v_power = _perm_power(v, idx, default=4)
        vehicles.append((v_power, crew_req))

    if not vehicles or not candidate_powers:
        non_token_attackers = len(candidate_powers)
        non_token_power_sum = sum(candidate_powers)
        return non_token_attackers, non_token_power_sum

    # Greedy crew: try to crew highest-power vehicles first, using smallest-power creatures to pay crew.
    # Only crew if net damage gain is positive: vehicle_power > sum(crewer_powers_used).
    vehicles.sort(key=lambda t: t[0], reverse=True)

    pool = Counter(candidate_powers)
    crewed_vehicle_powers: List[int] = []

    def pick_smallest_powers(c: Counter, required: int) -> Optional[List[int]]:
        picked: List[int] = []
        total = 0
        for pw in sorted(c.keys()):
            cnt = c[pw]
            while cnt > 0 and total < required:
                picked.append(pw)
                total += pw
                cnt -= 1
            if total >= required:
                return picked
        return None

    for v_power, crew_req in vehicles:
        if sum(pool.values()) <= 0:
            break

        trial = pool.copy()
        picked = pick_smallest_powers(trial, crew_req)
        if not picked:
            continue

        cost = sum(picked)
        if v_power <= cost:
            continue  # not worth sacrificing attackers for this vehicle

        # commit: remove picked crewers from pool, add vehicle as attacker
        for pw in picked:
            trial[pw] -= 1
            if trial[pw] <= 0:
                del trial[pw]
        pool = trial
        crewed_vehicle_powers.append(v_power)

    remaining_creature_attackers = sum(pool.values())
    remaining_creature_power = sum(pw * cnt for pw, cnt in pool.items())

    non_token_attackers = remaining_creature_attackers + len(crewed_vehicle_powers)
    non_token_power_sum = remaining_creature_power + sum(crewed_vehicle_powers)
    return non_token_attackers, non_token_power_sum


def attackable_creature_powers(st: GameState, idx: CardIndex) -> List[int]:
    powers: List[int] = []
    for p in st.iter_permanents():
        if not (idx.is_creature_perm(p) or getattr(p, "is_creature_this_turn", False)):
            continue
        if p.tapped:
            continue
        if not can_use_creature_this_turn(st, idx, p):
            continue
        powers.append(_perm_power(p, idx, default=2))
    return powers


def attackable_tokens(st: GameState, idx: CardIndex) -> int:
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    return st.token_pool if haste_all else max(0, st.token_pool - st.tokens_created_this_turn)


def max_creature_power(st: GameState, idx: CardIndex) -> int:
    mx = 1 if st.token_pool > 0 else 0
    for p in st.iter_permanents():
        if not (idx.is_creature_perm(p) or getattr(p, "is_creature_this_turn", False)):
            continue
        mx = max(mx, _perm_power(p, idx, default=2))
    return mx


def evaluate_damage_this_turn(st: GameState, idx: CardIndex) -> int:
    """
    Computes this-turn combat damage contribution and records st.attackers_this_turn
    (used by transform logic such as "attacked with N creatures").
    """
    def battlefield_has(role: str) -> bool:
        return any(role in idx.roles_for_perm(p) for p in st.iter_permanents())

    non_token_attackers, non_token_power = _non_token_attack_stats_with_vehicle_crew(st, idx)

    # Tokens: subtract those tapped for mana this turn (mana.py increments tokens_tapped_for_mana)
    tok = max(0, attackable_tokens(st, idx) - st.tokens_tapped_for_mana)

    attackers = non_token_attackers + tok
    st.attackers_this_turn = attackers  # <-- important for transform rules

    if attackers <= 0:
        return 0

    nominal = non_token_power + tok

    # Beastmaster Ascension approx (+5/+5 if >=7 attackers)
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