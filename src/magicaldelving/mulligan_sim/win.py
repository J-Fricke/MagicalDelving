from __future__ import annotations

from .combat import can_use_creature_this_turn, global_haste_online
from .index import CardIndex
from .models import GameState


def count_ready_creatures_for_tap(st: GameState, idx: CardIndex) -> int:
    """Creatures that can be tapped right now (summoning sickness applies unless haste)."""
    n = 0
    for p in st.iter_permanents():
        if not idx.is_creature_perm(p):
            continue
        if p.tapped:
            continue
        if not can_use_creature_this_turn(st, idx, p):
            continue
        n += 1

    # Tokens can count as creatures for Halo Fountain.
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    tok = st.token_pool
    if not haste_all:
        tok = max(0, tok - st.tokens_created_this_turn)
    n += tok

    return n


def has_wincon_resolved(st: GameState, idx: CardIndex) -> bool:
    # unconditional you-win (non-activated)
    for p in st.iter_permanents():
        txt = (idx.oracle_for_perm(p) or "").lower()
        if ("you win the game" in txt or "wins the game" in txt) and ":" not in txt:
            return True

    # Halo Fountain explicit activation (approx; ignores color): 6 mana + 15 ready creatures
    halo_on_board = any((p.name or "").strip().lower() == "halo fountain" for p in st.iter_permanents())
    if halo_on_board:
        available_mana = st.lands_in_play + st.ramp_sources_in_play
        ready_creatures = count_ready_creatures_for_tap(st, idx)
        if available_mana >= 6 and ready_creatures >= 15:
            return True

    return False
