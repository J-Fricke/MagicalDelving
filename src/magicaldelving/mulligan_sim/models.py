from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Set


@dataclass(frozen=True)
class SimGoals:
    draw_by_turn: int
    win_by_turn: int
    damage_threshold: int


@dataclass(frozen=True)
class SimConfig:
    trials: int
    seed: Optional[int]
    max_turns: int


@dataclass
class GameState:
    turn: int
    hand: List[str]
    library: List[str]
    battlefield: Set[str]

    lands_in_play: int = 0
    ramp_sources_in_play: int = 0  # "static" sources (rocks/dorks) for now

    refills_resolved: int = 0
    cumulative_damage: int = 0

    # When each permanent entered (for summoning sickness)
    entered_turn: Dict[str, int] = None  # type: ignore[assignment]

    # Rough token modeling
    token_pool: int = 0
    tokens_created_this_turn: int = 0  # cannot attack/tap this turn unless haste

    # One-turn combat buff modeling (from finisher spells)
    finisher_boost: int = 0          # +X/+X applied to each attacker
    finisher_haste: bool = False     # allows same-turn attacks/taps
    finisher_trample: bool = False   # improves connect rate

    # Track bodies tapped for mana this turn (so they can't also attack)
    creatures_tapped_for_mana: int = 0
    tokens_tapped_for_mana: int = 0
    brigid_tapped_for_mana: bool = False

    def __post_init__(self) -> None:
        if self.entered_turn is None:
            self.entered_turn = {}