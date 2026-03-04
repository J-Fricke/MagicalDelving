from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..index import CardIndex
from ..models import GameState, Permanent
from ..transform import apply_first_main
from ..mana import compute_burst_mana_pools, compute_creature_tap_mana_pool, default_cast_policy
from ..rules.crew import crew_precombat
from ..engine.continuous import ensure_continuous_effects, mark_continuous_dirty


@dataclass
class MainManaCtx:
    tap_creature_pool: List[Tuple[int, int, int]]          # (power, pid, qty)
    burst_land_sources: List[Tuple[int, int, int]]         # (x, pid, qty)
    burst_creature_sources: List[Tuple[int, int, int]]     # (x, pid, qty)


def _seed_types_from_facts(p: Permanent, idx: CardIndex) -> None:
    """
    Ensure p.types/subtypes reflect printed card facts (needed because Permanent.is_creature() uses p.types).
    Tokens should already have types filled at creation time.
    """
    if not p.is_card:
        return

    tl = (idx.type_line_for_perm(p) or "").strip()
    if not tl:
        return

    parts = tl.split("—")
    left = parts[0].strip()
    right = parts[1].strip() if len(parts) > 1 else ""

    p.types = {w for w in left.replace("-", " ").split() if w}
    p.subtypes = {w for w in right.replace("-", " ").split() if w}


def _add_passive_generic_tokens(st: GameState, n: int) -> None:
    """
    Keeps your old 'token_makers add 1 token/turn' approximation, but as permanents (qty-stacks).
    Vanilla 1/1 Creature tokens, sick=True on entry.
    """
    if n <= 0:
        return
    pid = st.add_permanent("Creature Token", entered_turn=st.turn, is_card=False, qty=n)
    tp = st.battlefield[pid]
    tp.types = {"Creature"}
    tp.base_power = 1
    tp.base_toughness = 1
    tp.power = 1
    tp.toughness = 1
    tp.sick = True


def main_phase(st: GameState, idx: CardIndex, *, engine_online: bool) -> tuple[bool, MainManaCtx]:
    """
    Main phase 1:
      - first main transforms (flip before mana)
      - land drop
      - passive token growth (legacy approximation)
      - engine draw (legacy approximation)
      - mana + cast policy
    """
    # land drop(s)
    if st.land_drops_remaining > 0:
        for c in list(st.hand):
            if not idx.is_land(c):
                continue

            st.hand.remove(c)
            pid = st.add_permanent(c, entered_turn=st.turn, face=0, is_card=True, qty=1)
            p = st.battlefield[pid]
            _seed_types_from_facts(p, idx)

            is_land = idx.is_land_perm(p)
            is_creature = idx.is_creature_perm(p)
            is_burst = "BurstManaFromCreatures" in idx.roles_for_perm(p)

            if is_land and (not is_creature) and (not is_burst):
                st.lands_in_play += 1

            st.land_drops_remaining = max(0, st.land_drops_remaining - 1)
            break

    # passive token growth: each TokenMaker permanent in play (from prior turns) adds 1 token/turn
    token_makers = 0
    for p in st.iter_permanents():
        if "TokenMaker" not in idx.roles_for_perm(p):
            continue
        if p.entered_turn == st.turn:
            continue
        token_makers += p.qty
    if token_makers:
        _add_passive_generic_tokens(st, token_makers)

    # draw engine online?
    engine_online = engine_online or any("DrawEngine" in idx.roles_for_perm(p) for p in st.iter_permanents())
    if engine_online and st.library:
        st.hand.append(st.library.pop(0))

    # mana: static first (normal lands + static ramp count)
    available_mana = st.lands_in_play + st.ramp_sources_in_play

    tap_creature_pool = compute_creature_tap_mana_pool(st, idx)
    burst_land_sources, burst_creature_sources = compute_burst_mana_pools(st, idx)

    available_mana, tap_creature_pool, burst_land_sources, burst_creature_sources = default_cast_policy(
        st,
        idx,
        available_mana,
        tap_creature_pool,
        burst_land_sources,
        burst_creature_sources,
    )

    ctx = MainManaCtx(
        tap_creature_pool=tap_creature_pool,
        burst_land_sources=burst_land_sources,
        burst_creature_sources=burst_creature_sources,
    )

    return engine_online, ctx


def main_phase_one(st: GameState, idx: CardIndex, *, engine_online: bool) -> tuple[bool, MainManaCtx]:
    # first main transforms (flip before you make mana)
    apply_first_main(st, idx)
    (engine_online, ctx) = main_phase(st,idx, engine_online=engine_online)
    crew_precombat(st, idx)
    mark_continuous_dirty(st)
    ensure_continuous_effects(st, idx)

    return engine_online, ctx


def main_phase_two(st: GameState, idx: CardIndex, *, engine_online: bool) -> tuple[bool, MainManaCtx]:
    (engine_online, ctx) = main_phase(st, idx, engine_online=engine_online)

    return engine_online, ctx

