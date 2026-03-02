from __future__ import annotations

import random
from typing import List, Tuple

from .defaults import ACTION_ROLES
from .index import CardIndex


def london_mulligan(
        cards: List[str],
        idx: CardIndex,
        rng: random.Random,
        max_mulls: int,
) -> Tuple[List[str], List[str]]:
    """
    Better London mulligan heuristic:
      - reject 0-land and all-land/no-action hands
      - allow 1-land only with castable cheap ramp + early action
      - bottom intelligently (preserve mana + early action)
    """
    base = list(cards)

    def has_action(hand: List[str]) -> bool:
        for c in hand:
            if idx.is_land(c):
                continue
            if idx.roles(c) & ACTION_ROLES:
                return True
        return False

    def count_lands(hand: List[str]) -> int:
        return sum(1 for c in hand if idx.is_land(c))

    def castable_cheap_ramp(hand: List[str], lands: int) -> int:
        n = 0
        for c in hand:
            if idx.is_land(c):
                continue
            if "Ramp" not in idx.roles(c):
                continue
            mv = int(idx.mv(c))
            if mv <= 2 and mv <= lands:
                n += 1
        return n

    def early_action_count(hand: List[str], mana_now: int) -> int:
        n = 0
        for c in hand:
            if idx.is_land(c):
                continue
            if not (idx.roles(c) & ACTION_ROLES):
                continue
            if idx.mv(c) <= mana_now:
                n += 1
        return n

    def keepable(hand: List[str], mulls_used: int) -> bool:
        lands = count_lands(hand)
        nonlands = 7 - lands

        if lands == 0:
            return False
        if nonlands == 0:
            return False
        if lands >= 6 and nonlands <= 1:
            return False
        if not has_action(hand):
            return False

        ramp = castable_cheap_ramp(hand, lands)

        if lands == 1:
            if ramp <= 0:
                return False
            mana_now = lands + min(1, ramp)
            if early_action_count(hand, mana_now) <= 0:
                return False

        return True

    def bottom_cards(hand: List[str], mulls_used: int) -> None:
        desired_lands = 3
        while mulls_used > 0 and hand:
            lands_in_hand = [c for c in hand if idx.is_land(c)]
            roles_cache = {c: idx.roles(c) for c in hand}

            def worst_key(c: str):
                mv = idx.mv(c)
                roles = roles_cache.get(c, set())

                if idx.is_land(c):
                    if len(lands_in_hand) > desired_lands:
                        return (0, -mv)
                    return (5, -mv)

                if not roles:
                    return (1, -mv)

                if "Ramp" in roles:
                    if mv <= 2:
                        return (9, -mv)
                    return (3, -mv)

                if roles & {"DrawEngine", "Refill"}:
                    return (8, -mv)

                if roles & {"Finisher", "Wincon"}:
                    return (6, -mv)

                if roles & {"TokenMaker", "TokenBurst"}:
                    return (7, -mv)

                return (4, -mv)

            worst = min(hand, key=worst_key)
            hand.remove(worst)
            mulls_used -= 1

    last_hand: List[str] = []
    last_lib: List[str] = []
    mulls_used = 0

    for m in range(0, max_mulls + 1):
        d = base[:]
        rng.shuffle(d)
        hand = d[:7]
        lib = d[7:]

        last_hand, last_lib, mulls_used = hand, lib, m

        if keepable(hand, m):
            break

    bottom_cards(last_hand, mulls_used)
    return last_hand, last_lib