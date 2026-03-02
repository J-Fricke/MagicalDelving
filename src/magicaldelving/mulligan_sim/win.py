from __future__ import annotations

from .combat import global_haste_online, is_hasty_creature
from .index import CardIndex
from .models import GameState


def count_ready_creatures_for_tap(st: GameState, idx: CardIndex) -> int:
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    n = 0
    for c in st.battlefield:
        f = idx.facts(c)
        if not (f and f.is_creature):
            continue
        entered = st.entered_turn.get(c, 0)
        if entered >= st.turn:
            if not haste_all and not is_hasty_creature(c, idx):
                continue
        n += 1
    return n


def has_wincon_resolved(st: GameState, idx: CardIndex) -> bool:
    # unconditional you-win (non-activated)
    for c in st.battlefield:
        f = idx.facts(c)
        if not f:
            continue
        txt = (f.oracle_text or "").lower()
        if ("you win the game" in txt or "wins the game" in txt) and ":" not in txt:
            return True

    # Halo Fountain explicit activation (approx; ignores color): 6 mana + 15 ready creatures
    halo_on_board = any((c or "").strip().lower() == "halo fountain" for c in st.battlefield)
    if halo_on_board:
        available_mana = st.lands_in_play + st.ramp_sources_in_play
        ready_creatures = count_ready_creatures_for_tap(st, idx)
        if available_mana >= 6 and ready_creatures >= 15:
            return True

    return False