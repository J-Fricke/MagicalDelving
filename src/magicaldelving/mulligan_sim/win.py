from __future__ import annotations

import re
from typing import Optional, Tuple

from .combat import can_use_creature_this_turn, global_haste_online
from .index import CardIndex
from .mana import compute_burst_mana_pools
from .models import GameState

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
}

_WIN_TAP_CREATURES_RE = re.compile(
    r"tap\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen)\s+untapped\s+creatures\s+you\s+control\s*:\s*you\s+win\s+the\s+game",
    re.IGNORECASE,
)

_MANA_SYMBOL_RE = re.compile(r"\{([^}]+)\}")


def _parse_n(s: str) -> Optional[int]:
    if not s:
        return None
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    return _WORD_NUM.get(s)


def _parse_mana_cost_segment(cost_segment: str) -> int:
    """
    Very rough generic-mana equivalent of the segment before ':' in an activated ability.
    - {W}{U}{B}{R}{G}{C}{S} count as 1 each
    - {2} counts as 2
    - hybrid/phyrexian treated as 1
    - {X} treated as 0
    - {T} ignored
    """
    total = 0
    for sym in _MANA_SYMBOL_RE.findall(cost_segment or ""):
        s = sym.strip().upper()
        if s == "T":
            continue
        if s == "X":
            continue
        if s.isdigit():
            total += int(s)
            continue
        # phyrexian/hybrid like "W/P", "2/W" -> count as 1
        total += 1
    return total


def _activated_tap_creatures_win_req(oracle_text: str) -> Optional[Tuple[int, int]]:
    """
    Detect abilities like:
      "{W}{W}{W}, Tap fifteen untapped creatures you control: You win the game."
    Returns (required_mana_generic, required_untapped_creatures).
    """
    txt = oracle_text or ""
    m = _WIN_TAP_CREATURES_RE.search(txt)
    if not m:
        return None

    n = _parse_n(m.group(1))
    if n is None:
        return None

    # Take the part of the line before the "Tap ..." for mana parsing.
    # This is intentionally permissive.
    prefix = txt[: m.start()]
    # Often costs are split by commas; only parse the last chunk.
    prefix = prefix.split("\n")[-1]
    prefix = prefix.split(",")[0] if "Tap" in prefix else prefix

    mana_req = _parse_mana_cost_segment(prefix)
    return mana_req, n


def count_ready_creatures_for_tap(st: GameState, idx: CardIndex) -> int:
    """Creatures that can be tapped right now (summoning sickness applies unless haste)."""
    n = 0
    for p in st.iter_permanents():
        if not idx.is_creature_perm(p):
            continue
        if p.tapped:
            continue
        if not can_use_creature_this_turn(st, idx, p):
            continue
        n += 1

    # Tokens can count as creatures for tap costs.
    haste_all = global_haste_online(st, idx) or st.finisher_haste
    tok = st.token_pool
    if not haste_all:
        tok = max(0, tok - st.tokens_created_this_turn)
    n += tok

    return n


def has_wincon_resolved(st: GameState, idx: CardIndex) -> bool:
    # 1) Unconditional you-win (non-activated)
    for p in st.iter_permanents():
        txt = (idx.oracle_for_perm(p) or "").lower()
        if ("you win the game" in txt or "wins the game" in txt) and ":" not in txt:
            return True

    # 2) Activated "tap N untapped creatures you control: you win the game"
    ready_creatures = count_ready_creatures_for_tap(st, idx)

    # For mana, stay conservative: use only static mana + burst LAND sources (not creature-tap sources),
    # because the win ability itself typically consumes untapped creatures.
    burst_land_sources, _burst_creature_sources = compute_burst_mana_pools(st, idx)
    mana_available = st.lands_in_play + st.ramp_sources_in_play + sum(x for x, _ in burst_land_sources)

    for p in st.iter_permanents():
        req = _activated_tap_creatures_win_req(idx.oracle_for_perm(p) or "")
        if not req:
            continue
        mana_req, creature_req = req
        if ready_creatures >= creature_req and mana_available >= mana_req:
            return True

    return False
