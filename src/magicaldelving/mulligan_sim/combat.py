from __future__ import annotations

import re
from collections import Counter
from typing import List, Optional, Tuple

from .index import CardIndex
from .models import GameState, Permanent


_CREW_RE = re.compile(r"\bcrew\s+(\d+)\b", re.IGNORECASE)


# ---------------------------
# Type override helpers
# ---------------------------

def _ensure_type_override_fields(p: Permanent) -> None:
    # Back-compat if Permanent hasn’t been updated yet.
    if not hasattr(p, "type_overrides_add"):
        setattr(p, "type_overrides_add", set())
    if not hasattr(p, "type_overrides_remove"):
        setattr(p, "type_overrides_remove", set())
    if not hasattr(p, "clear_type_overrides_eot"):
        setattr(p, "clear_type_overrides_eot", False)


def _make_creature_until_eot(p: Permanent) -> None:
    _ensure_type_override_fields(p)
    p.type_overrides_add.add("Creature")
    p.clear_type_overrides_eot = True


# ---------------------------
# Haste / sickness
# ---------------------------

def global_haste_online(st: GameState, idx: CardIndex) -> bool:
    for p in st.iter_permanents():
        txt = (idx.oracle_for_perm(p) or "").lower()
        if "have haste" in txt and ("creatures you control" in txt or "creatures have haste" in txt):
            return True
    return False


def is_hasty_creature(p: Permanent, idx: CardIndex) -> bool:
    # creature-ness is handled via idx.is_creature_perm (which should consult type overrides)
    if not idx.is_creature_perm(p):
        return False
    return "haste" in (idx.oracle_for_perm(p) or "").lower()


def can_use_creature_this_turn(st: GameState, idx: CardIndex, p: Permanent) -> bool:
    """Eligibility for tap/attack this turn (summoning sickness + haste)."""
    if not idx.is_creature_perm(p):
        return False

    haste_all = global_haste_online(st, idx) or st.finisher_haste
    if p.entered_turn >= st.turn:
        return bool(haste_all or is_hasty_creature(p, idx))
    return True


# ---------------------------
# Power helpers
# ---------------------------

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


# ---------------------------
# Vehicle detection + crew
# ---------------------------

def _is_vehicle_perm(p: Permanent, idx: CardIndex) -> bool:
    f = idx.facts(p.name)
    tl = (getattr(f, "type_line", "") or "").lower() if f else ""
    if "vehicle" in tl:
        return True
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
    Once crewed, Vehicles are creatures. If the Vehicle entered this turn, it’s summoning-sick unless haste.
    """
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    if p.entered_turn >= st.turn:
        txt = (idx.oracle_for_perm(p) or "").lower()
        has_self_haste = "haste" in txt
        return bool(haste_all or has_self_haste)
    return True


def _select_smallest_crewers(crewers_sorted: List[Tuple[int, int]], required: int) -> Optional[List[Tuple[int, int]]]:
    """
    crewers_sorted: list of (power, pid) sorted ascending power.
    Return a minimal-by-power greedy set whose total power >= required, or None.
    """
    picked: List[Tuple[int, int]] = []
    total = 0
    for pw, pid in crewers_sorted:
        picked.append((pw, pid))
        total += pw
        if total >= required:
            return picked
    return None


def _non_token_attack_stats_with_vehicle_crew(st: GameState, idx: CardIndex) -> Tuple[int, int]:
    """
    Returns (non_token_attackers, non_token_power_sum) after optionally crewing Vehicles
    using untapped, eligible creature permanents that would otherwise attack.

    This MUTATES state:
      - taps crew creatures
      - sets Vehicles to Creature until EOT (type override)
    """
    # Eligible creature attackers (untapped, can attack)
    crewers: List[Tuple[int, int]] = []  # (power, pid)
    for p in st.iter_permanents():
        if not idx.is_creature_perm(p):
            continue
        if p.tapped:
            continue
        if not can_use_creature_this_turn(st, idx, p):
            continue
        crewers.append((_perm_power(p, idx, default=2), p.pid))
    crewers.sort(key=lambda t: t[0])  # smallest first

    # Vehicles eligible to crew into attackers (not already creatures)
    vehicles: List[Tuple[int, int, int]] = []  # (vehicle_power, crew_req, v_pid)
    for v in st.iter_permanents():
        if v.tapped:
            continue
        if not _is_vehicle_perm(v, idx):
            continue
        if idx.is_creature_perm(v):
            continue

        crew_req = _crew_requirement(v, idx)
        if not crew_req or crew_req <= 0:
            continue
        if not _can_attack_if_crewed_this_turn(st, idx, v):
            continue

        v_power = _perm_power(v, idx, default=4)
        vehicles.append((v_power, crew_req, v.pid))

    if not vehicles or not crewers:
        non_token_attackers = len(crewers)
        non_token_power_sum = sum(pw for pw, _ in crewers)
        return non_token_attackers, non_token_power_sum

    # Crew greedily: highest-power vehicles first; pay with smallest-power creatures.
    vehicles.sort(key=lambda t: t[0], reverse=True)

    crewed_vehicle_powers: List[int] = []

    # We will maintain crewers list and remove used pids.
    for v_power, crew_req, v_pid in vehicles:
        if not crewers:
            break

        picked = _select_smallest_crewers(crewers, crew_req)
        if not picked:
            continue

        cost = sum(pw for pw, _ in picked)

        # Only crew if net gain is positive (vehicle hits harder than the attackers you tap away)
        if v_power <= cost:
            continue

        # Commit:
        # 1) tap picked crewers (they no longer attack)
        picked_pids = {pid for _, pid in picked}
        for pid in picked_pids:
            cperm = st.battlefield.get(pid)
            if cperm:
                cperm.tapped = True

        # 2) make vehicle a creature until EOT (do NOT tap vehicle)
        vperm = st.battlefield.get(v_pid)
        if vperm:
            _make_creature_until_eot(vperm)

        crewed_vehicle_powers.append(v_power)

        # 3) remove picked crewers from remaining crewer list
        crewers = [(pw, pid) for pw, pid in crewers if pid not in picked_pids]

    # Remaining creature attackers are those crewers not tapped for crew
    remaining_creature_attackers = len(crewers)
    remaining_creature_power = sum(pw for pw, _ in crewers)

    non_token_attackers = remaining_creature_attackers + len(crewed_vehicle_powers)
    non_token_power_sum = remaining_creature_power + sum(crewed_vehicle_powers)
    return non_token_attackers, non_token_power_sum


# ---------------------------
# Public combat API
# ---------------------------

def attackable_creature_powers(st: GameState, idx: CardIndex) -> List[int]:
    powers: List[int] = []
    for p in st.iter_permanents():
        if not idx.is_creature_perm(p):
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
        if not idx.is_creature_perm(p):
            continue
        mx = max(mx, _perm_power(p, idx, default=2))
    return mx


def evaluate_damage_this_turn(st: GameState, idx: CardIndex) -> int:
    """
    Computes this-turn combat damage contribution and records st.attackers_this_turn
    (used by transform logic such as "attacked with N creatures").

    NOTE: This mutates state for Vehicle crewing (taps crewers, sets vehicle type override until EOT).
    """
    def battlefield_has(role: str) -> bool:
        return any(role in idx.roles_for_perm(p) for p in st.iter_permanents())

    non_token_attackers, non_token_power = _non_token_attack_stats_with_vehicle_crew(st, idx)

    tok = max(0, attackable_tokens(st, idx) - st.tokens_tapped_for_mana)

    attackers = non_token_attackers + tok
    st.attackers_this_turn = attackers

    if attackers <= 0:
        return 0

    nominal = non_token_power + tok

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
