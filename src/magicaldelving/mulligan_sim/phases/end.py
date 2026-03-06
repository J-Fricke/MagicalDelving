from __future__ import annotations

from ..index import CardIndex
from ..models import GameState
from ..transform import apply_end_step
from ..engine.state_mutators import run_cleanup


def end_phase(st: GameState, idx: CardIndex) -> None:
    """
    End phase:
      - end step transforms
      - cleanup (delayed effects + recompute + merge)
      - record attackers_last_turn
    """
    st.audit_phase = "END"
    apply_end_step(st, idx)

    # PERM_MUTATION and other recompute-side audit events should be attributed to cleanup.
    st.audit_phase = "CLEANUP"
    run_cleanup(st, idx)

    # record for next-turn checks
    st.attackers_last_turn = st.attackers_this_turn
