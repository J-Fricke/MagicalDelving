from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..index import CardIndex
from ..models import DelayedEffect, GameState, Permanent
from . import eligibility as elig
from . import keywords as kw
from . import types as ty


_CREW_RE = re.compile(r"\bcrew\s+(\d+)\b", re.IGNORECASE)


@dataclass
class CrewTap:
    pid: int
    n: int
    power: int
    opp_cost: int  # 0 if this unit wouldn't attack; else power


def _crew_requirement(p: Permanent, idx: CardIndex) -> Optional[int]:
    m = _CREW_RE.search(idx.oracle_for_perm(p) or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _is_vehicle(p: Permanent, idx: CardIndex) -> bool:
    return "Vehicle" in (idx.type_line_for_perm(p) or "")


def _take_units(st: GameState, pid: int, n: int) -> int:
    """
    Ensure there's a battlefield entry representing exactly n units from pid.
    Returns pid for the extracted group (may be the original pid).
    """
    p = st.battlefield[pid]
    if n <= 0:
        raise ValueError("n must be > 0")
    if n > p.qty:
        raise ValueError("not enough quantity")
    if n == p.qty:
        return pid
    return st.split_permanent(pid, n)


def _tap_units(st: GameState, pid: int, n: int = 1) -> List[int]:
    """
    Tap n units from pid (splitting if needed). Returns pids tapped (len == n).
    """
    tapped_pids: List[int] = []
    for _ in range(n):
        unit_pid = _take_units(st, pid, 1)
        st.battlefield[unit_pid].tapped = True
        tapped_pids.append(unit_pid)
    return tapped_pids


def _add_type_until_cleanup(st: GameState, pid: int, type_name: str) -> None:
    """
    Mutate current truth (p.types) and schedule cleanup revert.
    """
    p = st.battlefield.get(pid)
    if not p:
        return

    if type_name not in p.types:
        p.types.add(type_name)

        def _revert(state: GameState, _pid: int) -> None:
            pp = state.battlefield.get(_pid)
            if pp:
                pp.types.discard(type_name)

        p.delayed.append(DelayedEffect(timing="CLEANUP", fn=_revert))


def _opportunity_cost_if_tapped_for_crew(p: Permanent, st: GameState) -> int:
    """
    Crew can tap summoning-sick creatures; cost is only what you'd lose in combat damage this turn.
    """
    # If it could attack right now, assume we lose its (single-strike) power contribution.
    return p.power_int() if p.can_attack(st) else 0


def crew_precombat(st: GameState, idx: CardIndex) -> None:
    """
    Precombat crew step:
      - For each Vehicle with "Crew N", tap creatures to meet N (sick is allowed).
      - Animate vehicle by adding Creature type until cleanup.
    Card-agnostic + conservative: only crew if net gain is positive.

    TODO: add alpha/kolodin-like effects that animate without tapping crewers.
    """
    # Build list of vehicles we might crew
    vehicles: List[Tuple[int, Permanent, int]] = []  # (pid, perm, crewN)
    for pid, p in st.battlefield.items():
        if p.tapped:
            continue
        if not _is_vehicle(p, idx):
            continue
        crew_n = _crew_requirement(p, idx)
        if not crew_n or crew_n <= 0:
            continue
        # If it's already a creature (some other effect), skip crewing
        if elig.is_creature(p):
            continue
        vehicles.append((pid, p, crew_n))

    if not vehicles:
        return

    # Build pool of available crewers (untapped creatures; sickness doesn't matter for crew)
    crewers: List[Tuple[int, int, int]] = []  # (opp_cost, -power, pid)
    for pid, p in st.battlefield.items():
        if p.tapped:
            continue
        if not elig.is_creature(p):
            continue
        # crew can tap sick creatures; just need untapped creature
        pw = p.power_int()
        opp = _opportunity_cost_if_tapped_for_crew(p, st)
        # Sort: prefer zero-opportunity-cost, then highest power (fewer taps)
        crewers.append((opp, -pw, pid))

    crewers.sort()

    # Try crewing highest-power vehicles first (more likely net positive)
    vehicles.sort(key=lambda t: st.battlefield[t[0]].power_int(), reverse=True)

    for v_pid, v, crew_n in vehicles:
        # Re-fetch vehicle (may have split/changed)
        v = st.battlefield.get(v_pid)
        if not v or v.tapped:
            continue
        if elig.is_creature(v):
            continue

        # If vehicle entered this turn and no haste, it still can’t attack after crew; skip.
        if v.sick and not kw.has_haste(v, st):
            continue

        v_power = v.power_int()
        st.audit("CREW_ATTEMPT", vehicle_pid=v_pid, vehicle=v.name, crew=crew_n, vehicle_power=v_power)
        if v_power <= 0:
            continue

        # Select crewers to reach crew_n power
        picked: List[CrewTap] = []
        have = 0
        # Rebuild crewer list each vehicle (since we mutate/tap)
        live_crewers: List[Tuple[int, int, int]] = []
        for pid, p in st.battlefield.items():
            if p.tapped:
                continue
            if not elig.is_creature(p):
                continue
            pw = p.power_int()
            opp = _opportunity_cost_if_tapped_for_crew(p, st)
            live_crewers.append((opp, -pw, pid))
        live_crewers.sort()

        for opp_cost, neg_pw, c_pid in live_crewers:
            if have >= crew_n:
                break
            cp = st.battlefield.get(c_pid)
            if not cp or cp.tapped or not elig.is_creature(cp):
                continue
            pw = -neg_pw
            if pw <= 0:
                continue
            # tap one unit at a time (qty-aware)
            _tap_units(st, c_pid, 1)
            st.audit("TAP_FOR_CREW", crewer_pid=c_pid, crewer=cp.name if cp else None, power=pw, opp_cost=opp_cost)
            picked.append(CrewTap(pid=c_pid, n=1, power=pw, opp_cost=opp_cost))
            have += pw

        if have < crew_n:
            # couldn't pay crew
            st.audit("CREW_FAIL", vehicle_pid=v_pid, vehicle=v.name if v else None, crew=crew_n, have=have)
            continue

        # Net gain check: benefit (vehicle power) must beat what we gave up (opp costs)
        cost = sum(t.opp_cost for t in picked)
        if v_power <= cost:
            st.audit("CREW_SKIP_NET", vehicle_pid=v_pid, vehicle=v.name if v else None, vehicle_power=v_power, cost=cost)
            continue

        # Commit animation
        _add_type_until_cleanup(st, v_pid, ty.CREATURE)
        st.audit("CREW_SUCCESS", vehicle_pid=v_pid, vehicle=st.battlefield.get(v_pid).name if st.battlefield.get(v_pid) else None, crew=crew_n, have=have)
