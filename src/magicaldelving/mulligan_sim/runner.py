from __future__ import annotations

import random
from collections import Counter
from typing import Any, Dict, List, Optional

from .combat import evaluate_damage_this_turn
from .index import CardIndex
from .mana import (
    compute_burst_mana_pools,
    compute_tap_mana_pool,
    default_cast_policy,
)
from .models import GameState, SimConfig, SimGoals
from .mulligan import london_mulligan
from .win import has_wincon_resolved


def run_sim(
        deck,
        card_index: CardIndex,
        goals: SimGoals,
        cfg: SimConfig,
        max_mulls: int,
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
                    f = card_index.facts(c)
                    # Land-creatures (e.g., Dryad Arbor) are creatures => summoning sickness applies to tapping.
                    # Burst-from-creatures lands (e.g., Cradle/Itlimoc) are not "1-mana lands".
                    # So only increment lands_in_play for normal, non-creature, non-burst lands.
                    if f and (not f.is_creature) and ("BurstManaFromCreatures" not in card_index.roles(c)):
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

            # mana: static first (normal lands + static rocks)
            available_mana = st.lands_in_play + st.ramp_sources_in_play

            # 1-mana taps (dorks OR blanket enabler)
            tap_creature_powers, tap_tokens = compute_tap_mana_pool(st, card_index)

            # burst mana taps (Cradle/Itlimoc/Brigid-back style)
            burst_land_sources, burst_creature_sources = compute_burst_mana_pools(st, card_index)

            available_mana, tap_creature_powers, tap_tokens, burst_land_sources, burst_creature_sources = default_cast_policy(
                st,
                card_index,
                available_mana,
                tap_creature_powers,
                tap_tokens,
                burst_land_sources,
                burst_creature_sources,
            )

            if turn == goals.draw_by_turn:
                mana_for_check = (
                        st.lands_in_play
                        + st.ramp_sources_in_play
                        + len(tap_creature_powers)
                        + tap_tokens
                        + sum(burst_land_sources)
                        + sum(burst_creature_sources)
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