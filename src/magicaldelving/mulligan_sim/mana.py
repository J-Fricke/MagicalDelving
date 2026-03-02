from __future__ import annotations

from typing import List, Tuple

from .combat import (
    attackable_creature_powers,
    attackable_tokens,
    global_haste_online,
    is_hasty_creature,
    max_creature_power,
)
from .index import CardIndex
from .models import GameState
from .tokens import estimate_tokens_created_from_text


def has_creature_tap_mana_enabler(st: GameState, idx: CardIndex) -> bool:
    """True if any permanent grants creatures a tap-for-mana ability."""
    return any("CreatureTapManaEnabler" in idx.roles(c) for c in st.battlefield)


def _creature_is_ready_to_tap(st: GameState, idx: CardIndex, name: str) -> bool:
    f = idx.facts(name)
    if not (f and f.is_creature):
        return True
    entered = st.entered_turn.get(name, 0)
    if entered < st.turn:
        return True
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    return bool(haste_all or is_hasty_creature(name, idx))


def compute_tap_mana_pool(st: GameState, idx: CardIndex) -> Tuple[List[int], int]:
    """Return (creature_power_pool, token_count_pool) for 1-mana taps.

    - If a CreatureTapManaEnabler is present: any *ready* creature and token can tap for 1.
    - Otherwise: only *ready* ManaDorks can tap for 1.
    """
    if has_creature_tap_mana_enabler(st, idx):
        powers = attackable_creature_powers(st, idx)
        powers.sort()
        tok = attackable_tokens(st, idx)
        return powers, tok

    # No blanket enabler: only mana dorks (and land-creatures with tap-add) contribute.
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    powers: List[int] = []
    for c in st.battlefield:
        if "ManaDork" not in idx.roles(c):
            continue
        f = idx.facts(c)
        if not (f and f.is_creature):
            continue

        entered = st.entered_turn.get(c, 0)
        if entered >= st.turn and not (haste_all or is_hasty_creature(c, idx)):
            continue

        powers.append(max(0, int(f.power)) if f.power is not None else 2)

    powers.sort()
    return powers, 0


def compute_burst_mana_pools(st: GameState, idx: CardIndex) -> Tuple[List[int], List[int]]:
    """Return (land_like_sources, creature_sources) for BurstManaFromCreatures.

    Examples this is meant to catch:
      - Gaea's Cradle / Itlimoc, Cradle of the Sun (lands): add mana based on creature count
      - Brigid-back style creatures: add X based on (other) creatures you control

    Each entry in the returned lists is the amount produced by *one tap* of that source.
    """
    # Total creatures you control (includes tokens and summoning-sick creatures).
    total_creatures = st.token_pool
    for c in st.battlefield:
        f = idx.facts(c)
        if f and f.is_creature:
            total_creatures += 1

    land_sources: List[int] = []
    creature_sources: List[int] = []

    for c in st.battlefield:
        if "BurstManaFromCreatures" not in idx.roles(c):
            continue

        f = idx.facts(c)
        if not f:
            continue

        # If this permanent is a creature (including land-creatures), summoning sickness applies.
        if f.is_creature and not _creature_is_ready_to_tap(st, idx, c):
            continue

        txt = (f.oracle_text or "").lower()
        x = total_creatures
        if "other creatures you control" in txt:
            x = max(0, total_creatures - 1)

        if x <= 0:
            continue

        if f.is_creature:
            creature_sources.append(x)
        else:
            land_sources.append(x)

    land_sources.sort(reverse=True)
    creature_sources.sort(reverse=True)
    return land_sources, creature_sources


def default_cast_policy(
        st: GameState,
        idx: CardIndex,
        available_mana: int,
        tap_creature_powers: List[int],
        tap_tokens: int,
        burst_land_sources: List[int],
        burst_creature_sources: List[int],
) -> Tuple[int, List[int], int, List[int], List[int]]:
    """Generic casting policy with creature-tap + burst mana.

    available_mana: static mana (lands_in_play + static rocks)
    tap_creature_powers/tap_tokens: 1-mana taps available via dorks or blanket enabler
    burst_land_sources/burst_creature_sources: big taps based on creature count

    Returns updated pools after casting.
    """

    def has_role(card: str, role: str) -> bool:
        return role in idx.roles(card)

    def mana_now() -> int:
        return (
                available_mana
                + len(tap_creature_powers)
                + tap_tokens
                + sum(burst_land_sources)
                + sum(burst_creature_sources)
        )

    def eff_cost(card: str, mana_total: int) -> int:
        name = (card or "").strip().lower()
        if name == "finale of devastation":
            # only model the X>=10 finisher mode
            return 12 if mana_total >= 12 else 10_000
        return int(idx.mv(card))

    def _tap_burst_if_needed(target_cost: int) -> None:
        nonlocal available_mana, burst_land_sources, burst_creature_sources
        # Tap burst sources (big chunks) before spending 1-mana creature taps.
        while available_mana < target_cost and (burst_land_sources or burst_creature_sources):
            if burst_land_sources:
                available_mana += burst_land_sources.pop(0)
                continue
            if burst_creature_sources:
                available_mana += burst_creature_sources.pop(0)
                st.creatures_tapped_for_mana += 1
                st.brigid_tapped_for_mana = True

    def pay(cost: int) -> bool:
        nonlocal available_mana, tap_creature_powers, tap_tokens

        _tap_burst_if_needed(cost)

        if cost <= available_mana:
            available_mana -= cost
            return True

        need = cost - available_mana
        available_mana = 0

        while need > 0 and tap_creature_powers:
            tap_creature_powers.pop(0)
            st.creatures_tapped_for_mana += 1
            need -= 1

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
            if has_role(c, "Ramp"):
                return (1, idx.mv(c))
            if has_role(c, "DrawEngine"):
                return (2, idx.mv(c))
            if has_role(c, "Refill"):
                return (3, idx.mv(c))
            if has_role(c, "TokenMaker") or has_role(c, "TokenBurst"):
                return (4, idx.mv(c))
            if name in ("overwhelming stampede", "finale of devastation") or has_role(c, "Finisher"):
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
        if is_perm:
            st.battlefield.add(c)
            st.entered_turn[c] = st.turn

        # Ramp: only increment static sources for non-dorks and non-blanket enablers.
        if has_role(c, "Ramp"):
            r = idx.roles(c)
            if ("ManaDork" not in r) and ("CreatureTapManaEnabler" not in r):
                st.ramp_sources_in_play += 1
                if f and f.is_artifact and not f.is_creature and not f.is_land:
                    available_mana += 1

        # Refill
        if has_role(c, "Refill"):
            st.refills_resolved += 1
            for _ in range(2):
                if st.library:
                    st.hand.append(st.library.pop(0))

        # Tokens (rough)
        if (has_role(c, "TokenBurst") or has_role(c, "TokenMaker")) and f:
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
        if has_role(c, "Finisher") and f and (f.is_instant or f.is_sorcery):
            st.finisher_boost = max(st.finisher_boost, 3)

    return available_mana, tap_creature_powers, tap_tokens, burst_land_sources, burst_creature_sources