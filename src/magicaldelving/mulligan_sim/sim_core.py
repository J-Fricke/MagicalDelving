from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import Counter

from .deck_parser import Deck
from .card_facts import CardFacts


@dataclass(frozen=True)
class SimGoals:
    draw_by_turn: int = 5
    win_by_turn: int = 8
    damage_threshold: int = 120  # EDH table


@dataclass(frozen=True)
class SimConfig:
    trials: int = 50_000
    seed: Optional[int] = None


@dataclass
class GameState:
    turn: int
    hand: List[str]
    library: List[str]
    battlefield: Set[str]

    lands_in_play: int = 0
    ramp_sources_in_play: int = 0

    refills_resolved: int = 0
    cumulative_damage: int = 0


class CardIndex:
    """Light wrapper around card facts + roles."""

    def __init__(self, facts_roles: Dict[str, Tuple[CardFacts, Set[str]]]):
        self._m = facts_roles

    def facts(self, name: str) -> Optional[CardFacts]:
        v = self._m.get(name)
        return v[0] if v else None

    def roles(self, name: str) -> Set[str]:
        v = self._m.get(name)
        return set(v[1]) if v else set()

    def is_land(self, name: str) -> bool:
        f = self.facts(name)
        return bool(f and f.is_land)

    def mv(self, name: str) -> float:
        f = self.facts(name)
        return float(f.mana_value) if f else 0.0


def london_mulligan(
    cards: List[str],
    idx: CardIndex,
    rng: random.Random,
    max_mulls: int = 2,
) -> Tuple[List[str], List[str]]:
    """Simple London mulligan.

    Keep rule (default, deck-agnostic):
      - 2-5 lands
      - at least one nonland spell

    Bottom rule: trim extra lands beyond 3; otherwise bottom highest mana value.
    """

    base = list(cards)

    for mulls in range(0, max_mulls + 1):
        d = base[:]
        rng.shuffle(d)
        hand = d[:7]
        lib = d[7:]

        lands = sum(1 for c in hand if idx.is_land(c))
        keep = (2 <= lands <= 5) and any(not idx.is_land(c) for c in hand)

        if keep or mulls == max_mulls:
            for _ in range(mulls):
                lands_in_hand = [c for c in hand if idx.is_land(c)]
                if len(lands_in_hand) > 3:
                    hand.remove(lands_in_hand[0])
                else:
                    worst = max(hand, key=lambda x: idx.mv(x))
                    hand.remove(worst)
            return hand, lib

    raise RuntimeError("unreachable")


def default_cast_policy(st: GameState, idx: CardIndex, available_mana: int) -> int:
    """Generic casting policy using heuristic roles."""

    def has_role(card: str, role: str) -> bool:
        return role in idx.roles(card)

    while True:
        castable = [c for c in st.hand if (not idx.is_land(c)) and idx.mv(c) <= available_mana]
        if not castable:
            break

        def prio(c: str) -> Tuple[int, float]:
            if has_role(c, "Ramp"):
                return (1, idx.mv(c))
            if has_role(c, "DrawEngine"):
                return (2, idx.mv(c))
            if has_role(c, "Refill"):
                return (3, idx.mv(c))
            if has_role(c, "Wincon"):
                return (4, idx.mv(c))
            return (9, idx.mv(c))

        castable.sort(key=prio)
        c = castable[0]

        st.hand.remove(c)
        st.battlefield.add(c)
        available_mana -= int(idx.mv(c))

        # Apply generic effects
        if has_role(c, "Ramp"):
            # simple: +1 effective mana source going forward
            st.ramp_sources_in_play += 1

        if has_role(c, "Refill"):
            st.refills_resolved += 1
            # conservative: +2 cards now
            for _ in range(2):
                if st.library:
                    st.hand.append(st.library.pop(0))

    return available_mana


def evaluate_damage_this_turn(st: GameState, idx: CardIndex) -> int:
    def battlefield_has(role: str) -> bool:
        return any(role in idx.roles(c) for c in st.battlefield)

    threats = sum(1 for c in st.battlefield if "Damage" in idx.roles(c))
    nominal = 4 * threats

    through = int(nominal * (0.85 if battlefield_has("Evasion") else 0.60))
    extra = sum(1 for c in st.battlefield if "ExtraCombat" in idx.roles(c))
    if extra:
        through += int(through * 0.70 * extra)

    return max(0, through)


def has_wincon_resolved(st: GameState, idx: CardIndex) -> bool:
    return any("Wincon" in idx.roles(c) for c in st.battlefield)


def run_sim(
    deck: Deck,
    card_index: CardIndex,
    goals: SimGoals,
    cfg: SimConfig,
    max_mulls: int = 2,
) -> Dict[str, Any]:
    rng = random.Random(cfg.seed)

    draw_ok = 0
    win_ok = 0
    first_win_turns: List[int] = []

    # We simulate the library only (99 for a single commander / 98 for partners)
    base_cards = list(deck.library)

    for _ in range(cfg.trials):
        hand, lib = london_mulligan(base_cards, card_index, rng, max_mulls=max_mulls)

        st = GameState(turn=0, hand=hand, library=list(lib), battlefield=set())
        engine_online = False

        for turn in range(1, goals.win_by_turn + 1):
            st.turn = turn

            # Draw step (EDH draws on T1)
            if st.library:
                st.hand.append(st.library.pop(0))

            # Land drop
            for c in list(st.hand):
                if card_index.is_land(c):
                    st.hand.remove(c)
                    st.battlefield.add(c)
                    st.lands_in_play += 1
                    break

            # Draw engine online?
            engine_online = engine_online or any("DrawEngine" in card_index.roles(c) for c in st.battlefield)
            if engine_online and st.library:
                st.hand.append(st.library.pop(0))

            available_mana = st.lands_in_play + st.ramp_sources_in_play
            default_cast_policy(st, card_index, available_mana)

            if turn == goals.draw_by_turn:
                has_refill_in_hand_castable = any(
                    ("Refill" in card_index.roles(c)) and (card_index.mv(c) <= (st.lands_in_play + st.ramp_sources_in_play))
                    for c in st.hand
                )
                if engine_online or st.refills_resolved > 0 or has_refill_in_hand_castable:
                    draw_ok += 1

            if has_wincon_resolved(st, card_index):
                win_ok += 1
                first_win_turns.append(turn)
                break

            st.cumulative_damage += evaluate_damage_this_turn(st, card_index)
            if st.cumulative_damage >= goals.damage_threshold:
                win_ok += 1
                first_win_turns.append(turn)
                break

    dist = Counter(first_win_turns)

    return {
        "trials": cfg.trials,
        "draw_ok_rate": draw_ok / cfg.trials if cfg.trials else 0.0,
        "win_ok_rate": win_ok / cfg.trials if cfg.trials else 0.0,
        "first_win_turn_dist": {str(k): int(v) for k, v in dist.items()},
        "goals": {
            "draw_by_turn": goals.draw_by_turn,
            "win_by_turn": goals.win_by_turn,
            "damage_threshold": goals.damage_threshold,
        },
        "max_mulls": max_mulls,
    }
