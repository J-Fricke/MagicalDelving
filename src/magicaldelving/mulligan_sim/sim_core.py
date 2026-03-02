from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import Counter

from .deck_parser import Deck
from .card_facts import CardFacts


_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

ACTION_ROLES: Set[str] = {
    "DrawEngine",
    "Refill",
    "TokenMaker",
    "TokenBurst",
    "Finisher",
    "Wincon",
}

# Cards that enable "tap creatures for mana" and must NOT be treated as +1 static ramp sources
CREATURE_TAP_MANA_ENABLERS: Set[str] = {
    "cryptolith rite",
    "enduring vitality",
}

BRIGID_BACK_NAME = "brigid, doun's mind"

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
        max_mulls: int = 3,
) -> Tuple[List[str], List[str]]:
    """
    Better London mulligan heuristic:
      - reject 0-land and all-land/no-action hands
      - allow 1-land only with castable cheap ramp + early action
      - accept 2-5 lands if there's at least one "action" card
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

            def worst_key(c: str) -> Tuple[int, float]:
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


def _estimate_tokens_created_from_text(txt: str) -> int:
    t = (txt or "").lower()
    if "create" not in t or "token" not in t:
        return 0

    m = re.search(r"create\s+(\d+)\s+.*token", t)
    if m:
        try:
            return max(1, int(m.group(1)))
        except Exception:
            return 1

    m = re.search(r"create\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s+.*token", t)
    if m:
        return _WORD_NUM.get(m.group(1), 1)

    if re.search(r"create\s+x\s+.*token", t):
        return 4

    return 1


def _global_haste_online(st: GameState, idx: CardIndex) -> bool:
    for c in st.battlefield:
        f = idx.facts(c)
        if not f:
            continue
        txt = (f.oracle_text or "").lower()
        if "have haste" in txt and ("creatures you control" in txt or "creatures have haste" in txt):
            return True
    return False


def _is_hasty_creature(name: str, idx: CardIndex) -> bool:
    f = idx.facts(name)
    if not f or not f.is_creature:
        return False
    return "haste" in (f.oracle_text or "").lower()


def _attackable_creature_powers(st: GameState, idx: CardIndex) -> List[int]:
    haste_all = _global_haste_online(st, idx) or st.finisher_haste
    powers: List[int] = []
    for c in st.battlefield:
        f = idx.facts(c)
        if not (f and f.is_creature):
            continue

        entered = st.entered_turn.get(c, 0)
        if entered >= st.turn:
            if not haste_all and not _is_hasty_creature(c, idx):
                continue

        if f.power is not None:
            powers.append(max(0, int(f.power)))
        else:
            powers.append(2)
    return powers


def _attackable_tokens(st: GameState, idx: CardIndex) -> int:
    haste_all = _global_haste_online(st, idx) or st.finisher_haste
    return st.token_pool if haste_all else max(0, st.token_pool - st.tokens_created_this_turn)


def _has_creature_tap_mana_enabler(st: GameState) -> bool:
    for c in st.battlefield:
        if (c or "").strip().lower() in CREATURE_TAP_MANA_ENABLERS:
            return True
    return False


def _compute_creature_tap_mana_pool(st: GameState, idx: CardIndex) -> Tuple[List[int], int]:
    creature_powers = _attackable_creature_powers(st, idx)
    creature_powers.sort()  # tap lowest power first to preserve combat
    tokens = _attackable_tokens(st, idx)
    return creature_powers, tokens


def _max_creature_power(st: GameState, idx: CardIndex) -> int:
    mx = 1 if st.token_pool > 0 else 0
    for c in st.battlefield:
        f = idx.facts(c)
        if not (f and f.is_creature):
            continue
        if f.power is not None:
            mx = max(mx, int(f.power))
        else:
            mx = max(mx, 2)
    return mx


def _brigid_burst_mana_if_available(st: GameState, idx: CardIndex) -> int:
    """
    Optional: only matters if Brigid-back appears on battlefield in your model.
    Conservative: requires Brigid to be able to tap this turn, uses "attackable" counts only.
    """
    brigid_on_board = any((c or "").strip().lower() == BRIGID_BACK_NAME for c in st.battlefield)
    if not brigid_on_board:
        return 0

    # can she tap this turn?
    powers = _attackable_creature_powers(st, idx)
    if not powers:
        return 0

    atk_tokens = _attackable_tokens(st, idx)
    atk_creatures = len(powers)
    other = max(0, (atk_creatures + atk_tokens) - 1)
    return other


def default_cast_policy(
        st: GameState,
        idx: CardIndex,
        available_mana: int,
        tap_creature_powers: List[int],
        tap_tokens: int,
        brigid_burst_mana: int,
) -> Tuple[int, List[int], int, int]:
    """
    Casting policy with creature-tap mana support.

    available_mana: "static" mana (lands + rocks/dorks counter)
    tap_creature_powers/tap_tokens: mana generated by tapping bodies (1 each)
    brigid_burst_mana: one-shot burst if Brigid-back is available (usually 0)

    Returns updated (available_mana, tap_creature_powers, tap_tokens, brigid_burst_mana).
    """

    def has_role(card: str, role: str) -> bool:
        return role in idx.roles(card)

    def mana_now() -> int:
        return available_mana + brigid_burst_mana + len(tap_creature_powers) + tap_tokens

    def eff_cost(card: str, mana_total: int) -> int:
        name = (card or "").strip().lower()
        if name == "finale of devastation":
            # only model the X>=10 finisher mode
            return 12 if mana_total >= 12 else 10_000
        return int(idx.mv(card))

    def pay(cost: int) -> bool:
        nonlocal available_mana, tap_creature_powers, tap_tokens, brigid_burst_mana

        if cost <= available_mana:
            available_mana -= cost
            return True

        need = cost - available_mana
        available_mana = 0

        # Brigid burst first
        if brigid_burst_mana > 0 and need > 0:
            use = min(need, brigid_burst_mana)
            brigid_burst_mana -= use
            need -= use
            if use > 0:
                st.brigid_tapped_for_mana = True

        # then tap low-power creatures
        while need > 0 and tap_creature_powers:
            tap_creature_powers.pop(0)
            st.creatures_tapped_for_mana += 1
            need -= 1

        # then tap tokens
        if need > 0 and tap_tokens > 0:
            use_tok = min(need, tap_tokens)
            tap_tokens -= use_tok
            st.tokens_tapped_for_mana += use_tok
            need -= use_tok

        return need <= 0

    while True:
        total = mana_now()
        castable = [c for c in st.hand if (not idx.is_land(c)) and eff_cost(c, total) <= total]
        if not castable:
            break

        def prio(c: str) -> Tuple[int, float]:
            name = (c or "").strip().lower()
            if has_role(c, "Ramp"):
                return (1, idx.mv(c))
            if has_role(c, "DrawEngine"):
                return (2, idx.mv(c))
            if has_role(c, "Refill"):
                return (3, idx.mv(c))
            if has_role(c, "TokenMaker") or has_role(c, "TokenBurst"):
                return (4, idx.mv(c))
            if name in ("overwhelming stampede", "finale of devastation") or has_role(c, "Finisher"):
                return (5, idx.mv(c))
            return (9, idx.mv(c))

        castable.sort(key=prio)
        c = castable[0]
        name = (c or "").strip().lower()
        cost = eff_cost(c, total)

        if not pay(cost):
            break

        st.hand.remove(c)

        f = idx.facts(c)
        is_perm = bool(f and not (f.is_instant or f.is_sorcery))
        if is_perm:
            st.battlefield.add(c)
            st.entered_turn[c] = st.turn

        # Ramp: do NOT increment for creature-tap enablers
        if has_role(c, "Ramp") and name not in CREATURE_TAP_MANA_ENABLERS:
            st.ramp_sources_in_play += 1
            # rocks can be used immediately
            if f and f.is_artifact and not f.is_creature and not f.is_land:
                available_mana += 1

        # Refill
        if has_role(c, "Refill"):
            st.refills_resolved += 1
            for _ in range(2):
                if st.library:
                    st.hand.append(st.library.pop(0))

        # Tokens (rough)
        if (has_role(c, "TokenBurst") or has_role(c, "TokenMaker")) and f:
            created = _estimate_tokens_created_from_text(f.oracle_text)
            if created > 0:
                st.token_pool += created
                st.tokens_created_this_turn += created

        # Finale finisher mode (X>=10)
        if name == "finale of devastation":
            st.finisher_boost = max(st.finisher_boost, 10)
            st.finisher_haste = True

        # Stampede
        if name == "overwhelming stampede":
            x = _max_creature_power(st, idx)
            st.finisher_boost = max(st.finisher_boost, x)
            st.finisher_trample = True

        # Generic finisher spell
        if has_role(c, "Finisher") and f and (f.is_instant or f.is_sorcery):
            st.finisher_boost = max(st.finisher_boost, 3)

    return available_mana, tap_creature_powers, tap_tokens, brigid_burst_mana


def evaluate_damage_this_turn(st: GameState, idx: CardIndex) -> int:
    def battlefield_has(role: str) -> bool:
        return any(role in idx.roles(c) for c in st.battlefield)

    powers = _attackable_creature_powers(st, idx)
    powers.sort()

    # remove creatures tapped for mana (tap lowest power first to mirror our mana usage)
    tapped = min(st.creatures_tapped_for_mana, len(powers))
    if tapped > 0:
        powers = powers[tapped:]

    creature_power = sum(powers)

    # tokens are 1/1; subtract tokens tapped for mana
    atk_tokens = _attackable_tokens(st, idx)
    atk_tokens = max(0, atk_tokens - st.tokens_tapped_for_mana)

    attackers = len(powers) + atk_tokens
    if attackers <= 0:
        return 0

    nominal = creature_power + atk_tokens

    # Beastmaster Ascension approx (+5/+5 if >=7 attackers)
    ascension_on_board = any((c or "").strip().lower() == "beastmaster ascension" for c in st.battlefield)
    ascension_active = ascension_on_board and attackers >= 7
    boost = st.finisher_boost + (5 if ascension_active else 0)
    trample = st.finisher_trample or ascension_active

    if boost > 0:
        nominal += boost * attackers

    if battlefield_has("Evasion"):
        connect = 0.85
    elif trample:
        connect = 0.75
    else:
        connect = 0.60

    through = int(nominal * connect)

    extra = sum(1 for c in st.battlefield if "ExtraCombat" in idx.roles(c))
    if extra:
        through += int(through * 0.70 * extra)

    return max(0, through)


def _count_ready_creatures_for_tap(st: GameState, idx: CardIndex) -> int:
    haste_all = _global_haste_online(st, idx) or st.finisher_haste
    n = 0
    for c in st.battlefield:
        f = idx.facts(c)
        if not (f and f.is_creature):
            continue
        entered = st.entered_turn.get(c, 0)
        if entered >= st.turn:
            if not haste_all and not _is_hasty_creature(c, idx):
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
        ready_creatures = _count_ready_creatures_for_tap(st, idx)
        if available_mana >= 6 and ready_creatures >= 15:
            return True

    return False


def run_sim(
        deck: Deck,
        card_index: CardIndex,
        goals: SimGoals,
        cfg: SimConfig,
        max_mulls: int = 3,
) -> Dict[str, Any]:
    rng = random.Random()
    if cfg.seed is not None:
        rng.seed(cfg.seed)

    draw_ok_count = 0
    win_ok_count = 0
    first_win_turns: List[int] = []

    base_cards = list(deck.library)

    for _ in range(cfg.trials):
        hand, lib = london_mulligan(base_cards, card_index, rng, max_mulls=max_mulls)

        st = GameState(turn=0, hand=hand, library=list(lib), battlefield=set())
        engine_online = False
        win_turn: Optional[int] = None

        for turn in range(1, cfg.max_turns + 1):
            st.turn = turn

            # per-turn reset
            st.tokens_created_this_turn = 0
            st.finisher_boost = 0
            st.finisher_haste = False
            st.finisher_trample = False
            st.creatures_tapped_for_mana = 0
            st.tokens_tapped_for_mana = 0
            st.brigid_tapped_for_mana = False

            # draw step (EDH draws on T1)
            if st.library:
                st.hand.append(st.library.pop(0))

            # land drop
            for c in list(st.hand):
                if card_index.is_land(c):
                    st.hand.remove(c)
                    st.battlefield.add(c)
                    st.entered_turn[c] = st.turn
                    st.lands_in_play += 1
                    break

            # passive token growth: each TokenMaker permanent in play (from prior turns) adds 1 token/turn
            token_makers = 0
            for c in st.battlefield:
                if "TokenMaker" not in card_index.roles(c):
                    continue
                f = card_index.facts(c)
                if not f or (f.is_instant or f.is_sorcery):
                    continue
                if st.entered_turn.get(c, 0) >= st.turn:
                    continue
                token_makers += 1
            if token_makers:
                st.token_pool += token_makers
                st.tokens_created_this_turn += token_makers

            # draw engine online?
            engine_online = engine_online or any("DrawEngine" in card_index.roles(c) for c in st.battlefield)
            if engine_online and st.library:
                st.hand.append(st.library.pop(0))

            # mana: static first
            available_mana = st.lands_in_play + st.ramp_sources_in_play

            # creature-tap mana pools (Rite/Vitality)
            tap_creature_powers: List[int] = []
            tap_tokens: int = 0
            if _has_creature_tap_mana_enabler(st):
                tap_creature_powers, tap_tokens = _compute_creature_tap_mana_pool(st, card_index)

            # Brigid-back burst (only if she exists in the model)
            brigid_burst = _brigid_burst_mana_if_available(st, card_index)

            available_mana, tap_creature_powers, tap_tokens, brigid_burst = default_cast_policy(
                st, card_index, available_mana, tap_creature_powers, tap_tokens, brigid_burst
            )

            if turn == goals.draw_by_turn:
                # include creature-tap pools for "castable refill" check
                mana_for_check = (
                        st.lands_in_play
                        + st.ramp_sources_in_play
                        + (len(tap_creature_powers) + tap_tokens if _has_creature_tap_mana_enabler(st) else 0)
                        + brigid_burst
                )
                has_refill_in_hand_castable = any(
                    ("Refill" in card_index.roles(c)) and (card_index.mv(c) <= mana_for_check)
                    for c in st.hand
                )
                if engine_online or st.refills_resolved > 0 or has_refill_in_hand_castable:
                    draw_ok_count += 1

            if has_wincon_resolved(st, card_index):
                win_turn = turn
                break

            st.cumulative_damage += evaluate_damage_this_turn(st, card_index)
            if st.cumulative_damage >= goals.damage_threshold:
                win_turn = turn
                break

        if win_turn is not None:
            first_win_turns.append(win_turn)
            if win_turn <= goals.win_by_turn:
                win_ok_count += 1

    dist = Counter(first_win_turns)

    wins_total = len(first_win_turns)
    avg_win_turn_wins_only = (sum(first_win_turns) / wins_total) if wins_total else None
    avg_win_turn_capped = (
        (sum(first_win_turns) + (cfg.trials - wins_total) * (cfg.max_turns + 1)) / cfg.trials
        if cfg.trials else None
    )

    return {
        "trials": cfg.trials,
        "draw_ok_rate": draw_ok_count / cfg.trials if cfg.trials else 0.0,
        "draw_ok_count": draw_ok_count,
        "win_ok_rate": win_ok_count / cfg.trials if cfg.trials else 0.0,
        "win_ok_count": win_ok_count,
        "wins_total": wins_total,
        "avg_win_turn_wins_only": avg_win_turn_wins_only,
        "avg_win_turn_capped": avg_win_turn_capped,
        "sim_max_turns": cfg.max_turns,
        "first_win_turn_dist": {str(k): int(v) for k, v in sorted(dist.items())},
        "goals": {
            "draw_by_turn": goals.draw_by_turn,
            "win_by_turn": goals.win_by_turn,
            "damage_threshold": goals.damage_threshold,
        },
        "max_mulls": max_mulls,
    }