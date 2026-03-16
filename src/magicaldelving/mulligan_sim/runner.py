from __future__ import annotations

import random
import hashlib
from collections import Counter
from typing import Any, Dict, List, Optional

from .index import CardIndex
from .models import GameState, SimConfig, SimGoals
from .audit_log import AuditLog
from .mulligan import london_mulligan
from .win import has_wincon_resolved

from .phases.beginning import beginning_phase
from .phases.main import main_phase_one, MainManaCtx
from .phases.combat import evaluate_combat_step
from .phases.end import end_phase




def _should_audit(seed: int | None, trial_idx: int, rate: float) -> bool:
    """Deterministic sampling without consuming RNG state."""
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        return True
    key = f"{seed or 0}:{trial_idx}".encode("utf-8")
    v = int.from_bytes(hashlib.blake2b(key, digest_size=2).digest(), "big") / 65535.0
    return v < rate
def run_sim(
        deck,
        card_index: CardIndex,
        goals: SimGoals,
        cfg: SimConfig,
        max_mulls: int,
        *,
        audit_rate: float = 0.01,
        audit_max_replays: int = 50,
) -> Dict[str, Any]:
    rng = random.Random()
    if cfg.seed is not None:
        rng.seed(cfg.seed)

    draw_ok_count = 0
    win_ok_count = 0
    first_win_turns: List[int] = []

    base_cards = list(deck.library)

    replays: List[Dict[str, Any]] = []

    for trial_idx in range(cfg.trials):
        audit_this = _should_audit(cfg.seed, trial_idx, float(audit_rate)) and (len(replays) < int(audit_max_replays))
        pre_mull: List[Dict[str, Any]] = []
        def _audit_cb(kind: str, **data: Any) -> None:
            pre_mull.append({"kind": kind, **data})
        audit_cb = _audit_cb if audit_this else None
        hand, lib = london_mulligan(base_cards, card_index, rng, max_mulls=max_mulls, audit_cb=audit_cb)

        st = GameState(turn=0, hand=hand, library=list(lib))
        st.audit_enabled = bool(audit_this)
        if st.audit_enabled:
            st.audit_log = AuditLog(max_events=8000)
            st.audit_phase = "MULLIGAN"
            # Turn 0: start state + mulligan actions + end state.
            st.audit_log.capture_start_state(st)
            for ev in pre_mull:
                kind = ev.get("kind")
                data = {k: v for k, v in ev.items() if k != "kind"}
                st.audit(kind, **data)
            st.audit_log.capture_end_state(st)

        engine_online = False
        win_turn: Optional[int] = None
        win_reason: Optional[str] = None
        for turn in range(1, cfg.max_turns + 1):
            st.turn = turn

            if st.audit_enabled and st.audit_log is not None:
                # Start-of-turn baseline (before untap/draw/etc.).
                st.audit_log.capture_start_state(st)

            # Beginning phase: untap, clear per-turn flags, upkeep, draw, merge
            beginning_phase(st, card_index)

            # Main phase 1: transforms, land drop, passive tokens, engine draw, mana+casts
            engine_online, mana_ctx = main_phase_one(st, card_index, engine_online=engine_online)

            # Draw goal check at configured turn
            if turn == goals.draw_by_turn:
                mana_for_check = (
                        st.lands_in_play
                        + st.ramp_sources_in_play
                        + sum(qty for _pw, _pid, qty in mana_ctx.tap_creature_pool)
                        + sum(x * qty for x, _pid, qty in mana_ctx.burst_land_sources)
                        + sum(x * qty for x, _pid, qty in mana_ctx.burst_creature_sources)
                )
                has_refill_in_hand_castable = any(
                    ("Refill" in card_index.roles(c)) and (card_index.mv(c) <= mana_for_check)
                    for c in st.hand
                )
                if engine_online or st.refills_resolved > 0 or has_refill_in_hand_castable:
                    draw_ok_count += 1

            # Win check (alternate wincons)
            if has_wincon_resolved(st, card_index):
                if st.audit_enabled and st.audit_log is not None:
                    st.audit_log.capture_end_state(st)
                win_turn = turn
                win_reason = "wincon"
                break

            # Combat phase
            st.cumulative_damage += evaluate_combat_step(st, card_index)
            if st.cumulative_damage >= goals.damage_threshold:
                if st.audit_enabled and st.audit_log is not None:
                    st.audit_log.capture_end_state(st)
                win_turn = turn
                win_reason = "damage"
                break

            # End phase: end step transforms + cleanup + merge + attackers_last_turn
            end_phase(st, card_index)

            if st.audit_enabled and st.audit_log is not None:
                st.audit_log.capture_end_state(st)

        if st.audit_enabled:
            replays.append({
                "trial": trial_idx,
                "win_turn": win_turn,
                "win_reason": win_reason,
                "cumulative_damage": st.cumulative_damage,
                "turns": st.export_replay_turns(),
            })

        if win_turn is not None:
            first_win_turns.append(win_turn)
            if win_turn <= goals.win_by_turn:
                win_ok_count += 1

    dist = Counter(first_win_turns)
    wins_total = len(first_win_turns)

    avg_win_turn_wins_only = (sum(first_win_turns) / wins_total) if wins_total else None
    avg_win_turn_capped = (
        (sum(first_win_turns) + (cfg.trials - wins_total) * (cfg.max_turns + 1)) / cfg.trials
        if cfg.trials
        else None
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
        "audit": {"rate": float(audit_rate), "max_replays": int(audit_max_replays), "replays": len(replays)},
        "replays": replays,
    }
