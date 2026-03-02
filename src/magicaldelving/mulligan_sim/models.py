from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional


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
class Permanent:
    """Per-permanent state (supports duplicates + transform + counters)."""

    pid: int
    name: str
    entered_turn: int

    # 0=front, 1=back, etc. (DFC / modal / split)
    face: int = 0

    tapped: bool = False

    # +1/+1, charge, etc.
    counters: Dict[str, int] = field(default_factory=dict)

    # Aura names attached (rough for now)
    auras: List[str] = field(default_factory=list)

    # --- NEW: per-turn combat projection knobs (deck logic can set these) ---
    is_creature_this_turn: bool = False
    attack_power_override_this_turn: Optional[int] = None


@dataclass
class GameState:
    turn: int
    hand: List[str]
    library: List[str]

    battlefield: Dict[int, Permanent] = field(default_factory=dict)
    next_pid: int = 1

    # "static" sources (modeled as counts for now)
    lands_in_play: int = 0
    ramp_sources_in_play: int = 0

    refills_resolved: int = 0
    cumulative_damage: int = 0

    # Rough token modeling
    token_pool: int = 0
    tokens_created_this_turn: int = 0

    # One-turn combat buff modeling (from finisher spells)
    finisher_boost: int = 0
    finisher_haste: bool = False
    finisher_trample: bool = False

    # Track taps for debug/summary
    creatures_tapped_for_mana: int = 0
    tokens_tapped_for_mana: int = 0
    burst_creatures_tapped: int = 0
    burst_lands_tapped: int = 0

    def add_permanent(self, name: str, entered_turn: int, face: int = 0) -> int:
        pid = self.next_pid
        self.next_pid += 1
        self.battlefield[pid] = Permanent(pid=pid, name=name, entered_turn=entered_turn, face=face)
        return pid

    def iter_permanents(self):
        return self.battlefield.values()
