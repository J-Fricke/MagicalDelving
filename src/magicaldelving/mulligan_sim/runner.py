from __future__ import annotations

import random
from collections import Counter
from typing import Any, Dict, List, Optional

from .combat import evaluate_damage_this_turn
from .index import CardIndex
from .mana import (
    compute_burst_mana_pools,
    compute_creature_tap_mana_pool,
    default_cast_policy,
)
from .models import GameState, SimConfig, SimGoals
from .mulligan import london_mulligan
from .transform import apply_end_step, apply_first_main, apply_upkeep
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

        st = GameState(turn=0, hand=hand, library=list(lib))
        engine_online = False
        win_turn: Optional[int] = None

        for turn in range(1, cfg.max_turns + 1):
            st.turn = turn

            # untap + reset per-turn flags
            for p in st.iter_permanents():
                p.tapped = False

            st.tokens_created_this_turn = 0
            st.finisher_boost = 0
            st.finisher_haste = False
            st.finisher_trample = False
            st.creatures_tapped_for_mana = 0
            st.tokens_tapped_for_mana = 0
            st.burst_creatures_tapped = 0
            st.burst_lands_tapped = 0
            st.attackers_this_turn = 0

            # upkeep transforms
            apply_upkeep(st, card_index)

            # draw step (EDH draws on T1)
            if st.library:
                st.hand.append(st.library.pop(0))

            # first main transforms (important for “flip before you make mana”)
            apply_first_main(st, card_index)

            # land drop
            for c in list(st.hand):
                if card_index.is_land(c):
                    st.hand.remove(c)
                    pid = st.add_permanent(c, entered_turn=st.turn, face=0)
                    p = st.battlefield[pid]

                    is_land = card_index.is_land_perm(p)
                    is_creature = card_index.is_creature_perm(p)
                    is_burst = "BurstManaFromCreatures" in card_index.roles_for_perm(p)
                    if is_land and (not is_creature) and (not is_burst):
                        st.lands_in_play += 1
                    break

            # passive token growth: each TokenMaker permanent in play (from prior turns) adds 1 token/turn
            token_makers = 0
            for p in st.iter_permanents():
                if "TokenMaker" not in card_index.roles_for_perm(p):
                    continue
                if p.entered_turn >= st.turn:
                    continue
                token_makers += 1
            if token_makers:
                st.token_pool += token_makers
                st.tokens_created_this_turn += token_makers

            # draw engine online?
            engine_online = engine_online or any(
                "DrawEngine" in card_index.roles_for_perm(p) for p in st.iter_permanents()
            )
            if engine_online and st.library:
                st.hand.append(st.library.pop(0))

            # mana: static first (normal lands + static ramp count)
            available_mana = st.lands_in_play + st.ramp_sources_in_play

            tap_creature_ids, tap_tokens = compute_creature_tap_mana_pool(st, card_index)
            burst_land_sources, burst_creature_sources = compute_burst_mana_pools(st, card_index)

            available_mana, tap_creature_ids, tap_tokens, burst_land_sources, burst_creature_sources = default_cast_policy(
                st,
                card_index,
                available_mana,
                tap_creature_ids,
                tap_tokens,
                burst_land_sources,
                burst_creature_sources,
            )

            if turn == goals.draw_by_turn:
                mana_for_check = (
                        st.lands_in_play
                        + st.ramp_sources_in_play
                        + len(tap_creature_ids)
                        + tap_tokens
                        + sum(x for x, _ in burst_land_sources)
                        + sum(x for x, _ in burst_creature_sources)
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

            # end step transforms (Growing Rites / Wedding Announcement counters / etc.)
            apply_end_step(st, card_index)

            # record for next-turn “last turn” checks
            st.attackers_last_turn = st.attackers_this_turn

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

    max_win_turn = max(first_win_turns) if first_win_turns else None

    avg_to_max_delta_wins_only = (
        (max_win_turn - avg_win_turn_wins_only)
        if (max_win_turn is not None and avg_win_turn_wins_only is not None)
        else None
    )

    avg_to_max_delta_capped = (
        (max_win_turn - avg_win_turn_capped)
        if (max_win_turn is not None and avg_win_turn_capped is not None)
        else None
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
        "max_win_turn": max_win_turn,
        "avg_to_max_delta_wins_only": avg_to_max_delta_wins_only,
        "avg_to_max_delta_capped": avg_to_max_delta_capped,
        "sim_max_turns": cfg.max_turns,
        "first_win_turn_dist": {str(k): int(v) for k, v in sorted(dist.items())},
        "goals": {
            "draw_by_turn": goals.draw_by_turn,
            "win_by_turn": goals.win_by_turn,
            "damage_threshold": goals.damage_threshold,
        },
        "max_mulls": max_mulls,
    }
