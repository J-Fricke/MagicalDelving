from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple


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
class DelayedEffect:
    """
    Delayed state changes (e.g., until end of turn reverts).
    timing: "CLEANUP" / "END_STEP" / etc.
    fn: (state, pid) -> None
    """
    timing: str
    fn: Callable[["GameState", int], None]


@dataclass
class Permanent:
    """
    A battlefield entry representing qty identical permanents.
    If a subset diverges (tapped/sick/counters/keywords/etc.), split into another Permanent.
    """

    pid: int
    name: str
    entered_turn: int

    qty: int = 1
    face: int = 0

    tapped: bool = False
    sick: bool = False

    counters: Dict[str, int] = field(default_factory=dict)
    auras: List[str] = field(default_factory=list)

    is_card: bool = True  # tokens are permanents; just not cards

    # Current truth (continuous effects / turn actions should keep these correct)
    types: Set[str] = field(default_factory=set)
    subtypes: Set[str] = field(default_factory=set)
    keywords: Set[str] = field(default_factory=set)

    base_power: Optional[int] = None
    base_toughness: Optional[int] = None
    power: Optional[int] = None
    toughness: Optional[int] = None

    # Per-turn override (crew, */X resolution, etc.)
    attack_power_override_this_turn: Optional[int] = None

    # Type overrides (until-EOT animation / crew / etc.)
    type_overrides_add: Set[str] = field(default_factory=set)
    type_overrides_remove: Set[str] = field(default_factory=set)
    clear_type_overrides_eot: bool = False

    delayed: List[DelayedEffect] = field(default_factory=list)

    # ---- predicates / helpers ----

    def is_creature(self) -> bool:
        if "Creature" in self.type_overrides_add:
            return True
        if "Creature" in self.type_overrides_remove:
            return False
        return "Creature" in self.types

    def has_keyword(self, kw: str) -> bool:
        return kw in self.keywords

    def has_haste(self, st: "GameState") -> bool:
        # Prefer keywords to reflect continuous effects; keep finisher_haste as a temporary global.
        return st.finisher_haste or ("Haste" in self.keywords)

    def can_tap(self, st: "GameState") -> bool:
        # “summoning sickness” eligibility (ignores tapped-ness)
        if not self.is_creature():
            return False
        if not self.sick:
            return True
        return self.has_haste(st)

    def can_attack(self, st: "GameState") -> bool:
        if not self.is_creature():
            return False
        if self.tapped:
            return False
        if not self.sick:
            return True
        return self.has_haste(st)

    def power_int(self) -> int:
        """
        No guessing. Prefer explicit override, then current power, then base_power, else 0.
        """
        if self.attack_power_override_this_turn is not None:
            try:
                return max(0, int(self.attack_power_override_this_turn))
            except Exception:
                return 0

        if self.power is not None:
            try:
                return max(0, int(self.power))
            except Exception:
                return 0

        if self.base_power is not None:
            try:
                return max(0, int(self.base_power))
            except Exception:
                return 0

        return 0

    def merge_signature(self) -> Tuple:
        """
        Conservative signature for merging identical stacks.
        Include all fields that would make two groups behave differently.
        """
        return (
            self.name,
            self.face,
            self.tapped,
            self.sick,
            self.entered_turn,
            self.is_card,
            tuple(sorted(self.types)),
            tuple(sorted(self.subtypes)),
            tuple(sorted(self.keywords)),
            self.base_power,
            self.base_toughness,
            self.power,
            self.toughness,
            self.attack_power_override_this_turn,
            tuple(sorted(self.type_overrides_add)),
            tuple(sorted(self.type_overrides_remove)),
            self.clear_type_overrides_eot,
            tuple(sorted(self.counters.items())),
            tuple(self.auras),
            # delayed effects prevent safe merge unless identical; usually empty at cleanup
            tuple((d.timing, getattr(d.fn, "__name__", "fn")) for d in self.delayed),
        )


@dataclass
class GameState:
    turn: int
    hand: List[str]
    library: List[str]

    battlefield: Dict[int, Permanent] = field(default_factory=dict)
    next_pid: int = 1

    # Event handlers
    handlers: Dict[str, List[Callable[["GameState", dict], None]]] = field(default_factory=dict)

    lands_in_play: int = 0
    ramp_sources_in_play: int = 0

    refills_resolved: int = 0
    cumulative_damage: int = 0

    finisher_boost: int = 0
    finisher_haste: bool = False
    finisher_trample: bool = False
    finisher_alpha: bool = False
    finisher_double_strike: bool = False

    attackers_this_turn: int = 0
    attackers_last_turn: int = 0

    creatures_tapped_for_mana: int = 0
    tokens_tapped_for_mana: int = 0
    burst_creatures_tapped: int = 0
    burst_lands_tapped: int = 0

    combat_diag: Dict[str, int] = field(default_factory=dict)

    def new_pid(self) -> int:
        pid = self.next_pid
        self.next_pid += 1
        return pid

    def add_permanent(self, name: str, entered_turn: int, face: int = 0, *, is_card: bool = True, qty: int = 1) -> int:
        pid = self.new_pid()
        self.battlefield[pid] = Permanent(
            pid=pid,
            name=name,
            entered_turn=entered_turn,
            face=face,
            is_card=is_card,
            qty=max(1, int(qty)),
            sick=True,
        )
        return pid

    def split_permanent(self, pid: int, n: int) -> int:
        """
        Split off n from a qty-stack into a new pid and return it.
        Caller then mutates the new Permanent as needed.
        """
        p = self.battlefield[pid]
        n = int(n)
        if n <= 0 or n >= p.qty:
            raise ValueError("split_permanent requires 0 < n < qty")

        p.qty -= n
        new_pid = self.new_pid()
        new_p = _copy_perm_for_new_pid(p, new_pid)
        new_p.qty = n
        self.battlefield[new_pid] = new_p
        return new_pid

    def iter_permanents(self) -> Iterable[Permanent]:
        return self.battlefield.values()

    def max_creature_power(self) -> int:
        mx = 0
        for p in self.iter_permanents():
            if not p.is_creature():
                continue
            mx = max(mx, p.power_int())
        return mx

    def add_handler(self, event: str, fn: Callable[["GameState", dict], None]) -> None:
        self.handlers.setdefault(event, []).append(fn)

    def emit(self, event: str, payload: dict) -> None:
        for fn in self.handlers.get(event, []):
            fn(self, payload)


def _copy_perm_for_new_pid(p: Permanent, new_pid: int) -> Permanent:
    return Permanent(
        pid=new_pid,
        name=p.name,
        entered_turn=p.entered_turn,
        qty=1,  # caller sets
        face=p.face,
        tapped=p.tapped,
        sick=p.sick,
        counters=dict(p.counters),
        auras=list(p.auras),
        is_card=p.is_card,
        types=set(p.types),
        subtypes=set(p.subtypes),
        keywords=set(p.keywords),
        base_power=p.base_power,
        base_toughness=p.base_toughness,
        power=p.power,
        toughness=p.toughness,
        attack_power_override_this_turn=p.attack_power_override_this_turn,
        type_overrides_add=set(p.type_overrides_add),
        type_overrides_remove=set(p.type_overrides_remove),
        clear_type_overrides_eot=p.clear_type_overrides_eot,
        delayed=list(p.delayed),
    )
