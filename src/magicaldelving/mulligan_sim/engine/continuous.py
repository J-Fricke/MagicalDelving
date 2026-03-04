from __future__ import annotations

from ..index import CardIndex
from ..models import GameState
from .state_mutators import recompute_continuous_effects


def mark_continuous_dirty(st: GameState) -> None:
    st.continuous_dirty = True


def ensure_continuous_effects(st: GameState, idx: CardIndex) -> None:
    if not getattr(st, "continuous_dirty", True):
        return
    recompute_continuous_effects(st, idx)
    st.continuous_dirty = False
