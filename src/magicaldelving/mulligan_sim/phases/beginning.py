from __future__ import annotations

from ..index import CardIndex
from ..models import GameState
from ..transform import apply_upkeep
from ..engine.state_mutators import beginning_merge


def beginning_phase(st: GameState, idx: CardIndex) -> None:
    """
    Beginning phase:
      - Untap
      - Clear per-turn overrides/flags
      - Sickness update
      - Merge identical groups
      - Upkeep triggers/transforms
      - Draw step (EDH draws on turn 1)
    """
    # Untap + clear per-turn overrides
    for p in st.iter_permanents():
        p.tapped = False
        p.attack_power_override_this_turn = None

        # sickness wears off if it didn't enter this turn
        if p.entered_turn < st.turn:
            p.sick = False

    # Clear per-turn flags/counters
    st.finisher_boost = 0
    st.finisher_haste = False
    st.finisher_trample = False
    st.finisher_alpha = False
    st.finisher_double_strike = False

    st.creatures_tapped_for_mana = 0
    st.tokens_tapped_for_mana = 0
    st.burst_creatures_tapped = 0
    st.burst_lands_tapped = 0
    st.attackers_this_turn = 0

    # Merge after untap/sick update
    beginning_merge(st)

    # Upkeep transforms/triggers
    apply_upkeep(st, idx)

    # Draw step (EDH draws on T1)
    if st.library:
        st.hand.append(st.library.pop(0))
