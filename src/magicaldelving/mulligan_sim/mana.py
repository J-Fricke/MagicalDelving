from __future__ import annotations

from typing import List, Tuple

from .combat import can_use_creature_this_turn, max_creature_power
from .index import CardIndex
from .models import GameState
from .tokens import estimate_tokens_created_from_text


def has_creature_tap_mana_enabler(st: GameState, idx: CardIndex) -> bool:
    """True if any permanent grants creatures a tap-for-mana ability."""
    for p in st.iter_permanents():
        if "CreatureTapManaEnabler" in idx.roles_for_perm(p):
            return True
    return False


def compute_creature_tap_mana_pool(st: GameState, idx: CardIndex) -> Tuple[List[int], int]:
    """Return (creature_perm_ids, token_count) available for 1-mana taps.

    - If a CreatureTapManaEnabler is present: any *ready* creature can tap for 1.
    - Otherwise: only *ready* ManaDorks can tap for 1.

    We return creature ids sorted by power ascending so we tap low-power bodies first.
    """
    blanket = has_creature_tap_mana_enabler(st, idx)

    pool: List[Tuple[int, int]] = []  # (power, pid)
    for pid, p in st.battlefield.items():
        if not idx.is_creature_perm(p):
            continue
        if p.tapped:
            continue
        if not can_use_creature_this_turn(st, idx, p):
            continue

        roles = idx.roles_for_perm(p)
        if (not blanket) and ("ManaDork" not in roles):
            continue

        f = idx.facts(p.name)
        power = int(f.power) if (f and f.power is not None) else 2
        pool.append((max(0, power), pid))

    pool.sort(key=lambda x: x[0])
    creature_ids = [pid for _, pid in pool]

    token_count = 0
    if blanket:
        token_count = st.token_pool
        # no global haste => tokens created this turn can't tap
        # (we treat "finisher_haste" as global haste for this sim)
        if not st.finisher_haste:
            token_count = max(0, token_count - st.tokens_created_this_turn)

    return creature_ids, token_count


def compute_burst_mana_pools(st: GameState, idx: CardIndex) -> Tuple[List[Tuple[int, int]], List[Tuple[int, int]]]:
    """Return (land_like_sources, creature_sources) for BurstManaFromCreatures.

    Each entry: (mana_amount, perm_id).
    Amount computed from oracle text:
      - "for each creature you control" => X = total creatures
      - "other creatures you control" => X = total creatures - 1
    """
    total_creatures = st.token_pool
    for p in st.iter_permanents():
        if idx.is_creature_perm(p):
            total_creatures += 1

    land_sources: List[Tuple[int, int]] = []
    creature_sources: List[Tuple[int, int]] = []

    for pid, p in st.battlefield.items():
        if p.tapped:
            continue
        if "BurstManaFromCreatures" not in idx.roles_for_perm(p):
            continue

        if idx.is_creature_perm(p) and not can_use_creature_this_turn(st, idx, p):
            continue

        txt = (idx.oracle_for_perm(p) or "").lower()
        x = total_creatures
        if "other creatures you control" in txt:
            x = max(0, total_creatures - 1)

        if x <= 0:
            continue

        if idx.is_creature_perm(p):
            creature_sources.append((x, pid))
        else:
            land_sources.append((x, pid))

    land_sources.sort(key=lambda t: t[0], reverse=True)
    creature_sources.sort(key=lambda t: t[0], reverse=True)
    return land_sources, creature_sources


def default_cast_policy(
        st: GameState,
        idx: CardIndex,
        available_mana: int,
        tap_creature_ids: List[int],
        tap_tokens: int,
        burst_land_sources: List[Tuple[int, int]],
        burst_creature_sources: List[Tuple[int, int]],
) -> Tuple[int, List[int], int, List[Tuple[int, int]], List[Tuple[int, int]]]:
    """Generic casting policy with creature-tap + burst mana."""

    def has_role_name(name: str, role: str) -> bool:
        return role in idx.roles(name)

    def mana_now() -> int:
        return (
                available_mana
                + len(tap_creature_ids)
                + tap_tokens
                + sum(x for x, _ in burst_land_sources)
                + sum(x for x, _ in burst_creature_sources)
        )

    def eff_cost(card: str, mana_total: int) -> int:
        name = (card or "").strip().lower()
        if name == "finale of devastation":
            return 12 if mana_total >= 12 else 10_000
        return int(idx.mv(card))

    def _tap_perm(pid: int) -> None:
        p = st.battlefield.get(pid)
        if p:
            p.tapped = True

    def pay(cost: int) -> bool:
        nonlocal available_mana, tap_creature_ids, tap_tokens, burst_land_sources, burst_creature_sources

        # Tap burst sources first (big chunks)
        while available_mana < cost and (burst_land_sources or burst_creature_sources):
            if burst_land_sources:
                x, pid = burst_land_sources.pop(0)
                available_mana += x
                _tap_perm(pid)
                st.burst_lands_tapped += 1
                continue
            if burst_creature_sources:
                x, pid = burst_creature_sources.pop(0)
                available_mana += x
                _tap_perm(pid)
                st.burst_creatures_tapped += 1
                continue

        if cost <= available_mana:
            available_mana -= cost
            return True

        need = cost - available_mana
        available_mana = 0

        # Tap 1-mana creatures
        while need > 0 and tap_creature_ids:
            pid = tap_creature_ids.pop(0)
            _tap_perm(pid)
            st.creatures_tapped_for_mana += 1
            need -= 1

        # Tap tokens
        if need > 0 and tap_tokens > 0:
            use_tok = min(need, tap_tokens)
            tap_tokens -= use_tok
            st.tokens_tapped_for_mana += use_tok
            need -= use_tok

        return need <= 0

    while True:
        total = mana_now()
        castable = [c for c in st.hand if (not idx.is_land(c)) and eff_cost(c, total) <= total]
        if not castable:
            break

        def prio(c: str):
            name = (c or "").strip().lower()
            if has_role_name(c, "Ramp"):
                return (1, idx.mv(c))
            if has_role_name(c, "DrawEngine"):
                return (2, idx.mv(c))
            if has_role_name(c, "Refill"):
                return (3, idx.mv(c))
            if has_role_name(c, "TokenMaker") or has_role_name(c, "TokenBurst"):
                return (4, idx.mv(c))
            if name in ("overwhelming stampede", "finale of devastation") or has_role_name(c, "Finisher"):
                return (5, idx.mv(c))
            return (9, idx.mv(c))

        castable.sort(key=prio)
        c = castable[0]
        name = (c or "").strip().lower()
        cost = eff_cost(c, total)

        if not pay(cost):
            break

        st.hand.remove(c)

        f = idx.facts(c)
        is_perm = bool(f and not (f.is_instant or f.is_sorcery))

        new_perm = None
        if is_perm:
            new_pid = st.add_permanent(c, entered_turn=st.turn, face=0)
            new_perm = st.battlefield.get(new_pid)

        # Ramp: only increment static sources for non-dorks, non-enablers, non-burst.
        if has_role_name(c, "Ramp"):
            roles = idx.roles_for_perm(new_perm) if new_perm else idx.roles(c)
            if ("ManaDork" not in roles) and ("CreatureTapManaEnabler" not in roles) and ("BurstManaFromCreatures" not in roles):
                st.ramp_sources_in_play += 1
                # artifact ramp can be used immediately
                if new_perm is not None and idx.is_artifact_perm(new_perm) and (not idx.is_creature_perm(new_perm)) and (not idx.is_land_perm(new_perm)):
                    available_mana += 1

        # Refill
        if has_role_name(c, "Refill"):
            st.refills_resolved += 1
            for _ in range(2):
                if st.library:
                    st.hand.append(st.library.pop(0))

        # Tokens (rough)
        if (has_role_name(c, "TokenBurst") or has_role_name(c, "TokenMaker")) and f:
            created = estimate_tokens_created_from_text(f.oracle_text)
            if created > 0:
                st.token_pool += created
                st.tokens_created_this_turn += created

        # Finale finisher mode (X>=10)
        if name == "finale of devastation":
            st.finisher_boost = max(st.finisher_boost, 10)
            st.finisher_haste = True

        # Stampede
        if name == "overwhelming stampede":
            x = max_creature_power(st, idx)
            st.finisher_boost = max(st.finisher_boost, x)
            st.finisher_trample = True

        # Generic finisher spell
        if has_role_name(c, "Finisher") and f and (f.is_instant or f.is_sorcery):
            st.finisher_boost = max(st.finisher_boost, 3)

    return available_mana, tap_creature_ids, tap_tokens, burst_land_sources, burst_creature_sources
