# sim_core.py
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple, Any
from collections import Counter

from deck_parser import Deck
import card_db

@dataclass(frozen=True)
class SimGoals:
    draw_by_turn: int = 5
    win_by_turn: int = 8
    damage_threshold: int = 120  # EDH table

@dataclass(frozen=True)
class SimConfig:
    trials: int = 100_000
    seed: int = 1

@dataclass
class GameState:
    turn: int
    hand: List[str]
    library: List[str]
    battlefield: Set[str]

    lands_in_play: int = 0
    # Simple mana: lands + count(ramp permanents) + treasures (if you model them as roles later)
    ramp_sources_in_play: int = 0

    # Metrics bookkeeping
    refills_resolved: int = 0
    cumulative_damage: int = 0

def london_mulligan(cards: List[str], rng: random.Random, max_mulls: int = 3) -> Tuple[List[str], List[str]]:
    """
    7/7/6/5...: we simulate by drawing 7 and bottoming mulls cards.
    Keep rule: 2-5 lands and at least one nonland.
    """
    base = list(cards)
    for mulls in range(0, max_mulls + 1):
        d = base[:]
        rng.shuffle(d)
        hand = d[:7]
        lib = d[7:]

        lands = sum(1 for c in hand if card_db.is_land(c))
        keep = (2 <= lands <= 5) and any(not card_db.is_land(c) for c in hand)

        if keep or mulls == max_mulls:
            # bottom mulls cards: trim extra lands beyond 3, else highest MV
            for _ in range(mulls):
                lands_in_hand = [c for c in hand if card_db.is_land(c)]
                if len(lands_in_hand) > 3:
                    hand.remove(lands_in_hand[0])
                else:
                    worst = max(hand, key=lambda x: card_db.mv(x))
                    hand.remove(worst)
            return hand, lib

    raise RuntimeError("unreachable")

def default_cast_policy(st: GameState, available_mana: int) -> int:
    """
    Generic casting policy:
      - cast as many Ramp permanents as possible (roles contains Ramp)
      - then cast a DrawEngine permanent if possible
      - then cast a Refill if possible
      - otherwise cast cheapest card
    Returns remaining mana.
    """
    def role(card: str, r: str) -> bool:
        return r in card_db.get(card).roles

    # Build a list of castable candidates and pick by priority
    while True:
        castable = [c for c in st.hand if not card_db.is_land(c) and card_db.mv(c) <= available_mana]
        if not castable:
            break

        def prio(c: str) -> Tuple[int, int]:
            if role(c, "Ramp"):
                return (1, card_db.mv(c))
            if role(c, "DrawEngine"):
                return (2, card_db.mv(c))
            if role(c, "Refill"):
                return (3, card_db.mv(c))
            return (9, card_db.mv(c))

        castable.sort(key=prio)
        c = castable[0]

        st.hand.remove(c)
        st.battlefield.add(c)
        available_mana -= card_db.mv(c)

        # Apply generic effects from roles
        if role(c, "Ramp"):
            st.ramp_sources_in_play += 1

        if role(c, "Refill"):
            st.refills_resolved += 1
            # conservative: immediate +2 cards (wheels can be handled later by a richer model)
            for _ in range(2):
                if st.library:
                    st.hand.append(st.library.pop(0))

    return available_mana

def evaluate_damage_this_turn(st: GameState) -> int:
    """
    Deck-agnostic conservative damage estimate:
    - each card with role "Damage" on battlefield contributes 4 nominal damage
    - if you have any "Evasion" role on battlefield, 85% gets through, else 60%
    - each "ExtraCombat" role on battlefield adds 70% of that again
    """
    def has_role(r: str) -> bool:
        return any(r in card_db.get(c).roles for c in st.battlefield)

    threats = sum(1 for c in st.battlefield if "Damage" in card_db.get(c).roles)
    nominal = 4 * threats

    through = int(nominal * (0.85 if has_role("Evasion") else 0.60))
    extra = sum(1 for c in st.battlefield if "ExtraCombat" in card_db.get(c).roles)
    if extra:
        through += int(through * 0.70 * extra)

    return max(0, through)

def has_wincon_resolved(st: GameState) -> bool:
    """
    Wincon definition (agnostic):
    If any battlefield card has role "Wincon", you win.
    (Later you can model 'cast this turn' vs 'must activate'.)
    """
    return any("Wincon" in card_db.get(c).roles for c in st.battlefield)

def run_sim(deck: Deck, goals: SimGoals, cfg: SimConfig) -> Dict[str, Any]:
    rng = random.Random(cfg.seed)

    draw_ok = 0
    win_ok = 0
    first_win_turns: List[int] = []

    for _ in range(cfg.trials):
        # shuffle full 100; if you want commander-zone support later, plug that in here
        cards = list(deck.cards)
        rng.shuffle(cards)

        hand, lib = london_mulligan(cards, rng)

        st = GameState(turn=0, hand=hand, library=lib, battlefield=set())
        engine_online = False

        for turn in range(1, goals.win_by_turn + 1):
            st.turn = turn

            # Draw step (EDH draws on T1)
            if st.library:
                st.hand.append(st.library.pop(0))

            # Land drop
            for c in list(st.hand):
                if card_db.is_land(c):
                    st.hand.remove(c)
                    st.battlefield.add(c)
                    st.lands_in_play += 1
                    break

            # Determine if draw engine online (agnostic: any DrawEngine permanent on battlefield)
            engine_online = engine_online or any("DrawEngine" in card_db.get(c).roles for c in st.battlefield)

            # If engine online, draw 1 extra per turn (floor). Later you can make this per-card.
            if engine_online and st.library:
                st.hand.append(st.library.pop(0))

            available_mana = st.lands_in_play + st.ramp_sources_in_play

            # Cast
            default_cast_policy(st, available_mana)

            # End-step metrics
            if turn == goals.draw_by_turn:
                has_engine = engine_online
                has_refill_in_hand_castable = any(("Refill" in card_db.get(c).roles) and (card_db.mv(c) <= (st.lands_in_play + st.ramp_sources_in_play)) for c in st.hand)
                if has_engine or st.refills_resolved > 0 or has_refill_in_hand_castable:
                    draw_ok += 1

            # Win checks
            if has_wincon_resolved(st):
                win_ok += 1
                first_win_turns.append(turn)
                break

            st.cumulative_damage += evaluate_damage_this_turn(st)
            if st.cumulative_damage >= goals.damage_threshold:
                win_ok += 1
                first_win_turns.append(turn)
                break

    return {
        "trials": cfg.trials,
        "draw_ok_rate": draw_ok / cfg.trials,
        "win_ok_rate": win_ok / cfg.trials,
        "first_win_turn_dist": Counter(first_win_turns),
    }