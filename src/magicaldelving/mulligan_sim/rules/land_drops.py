# src/magicaldelving/mulligan_sim/rules/land_drops.py
from __future__ import annotations

import re
from typing import Iterable, Tuple

_WORD = {
    "a": 1, "an": 1, "one": 1,
    "two": 2, "three": 3, "four": 4, "five": 5,
}

# Static/continuous: "You may play two additional lands on each of your turns."
_STATIC_RE = re.compile(
    r"you may play\s+(?P<n>an|a|one|two|three|four|five|\d+)\s+additional\s+lands?\s+"
    r"(?:on\s+each\s+of\s+your\s+turns|on\s+each\s+your\s+turns|each\s+turn|on\s+each\s+turn)",
    re.IGNORECASE,
)

# One-shot: "You may play an additional land this turn."
_THIS_TURN_RE = re.compile(
    r"you may play\s+(?P<n>an|a|one|two|three|four|five|\d+)\s+additional\s+lands?\s+this\s+turn",
    re.IGNORECASE,
)

# Catch-all detector for auditing
_HAS_PHRASE_RE = re.compile(r"additional\s+lands?", re.IGNORECASE)


def _parse_n(raw: str) -> int:
    s = (raw or "").strip().lower()
    if s.isdigit():
        return int(s)
    return _WORD.get(s, 1)


def scan_extra_land_drops_from_text(txt: str) -> Tuple[int, int, bool]:
    """
    Returns (extra_each_turn, extra_this_turn, saw_additional_land_phrase).
    """
    t = txt or ""
    saw = bool(_HAS_PHRASE_RE.search(t))
    each_turn = 0
    this_turn = 0

    for m in _STATIC_RE.finditer(t):
        each_turn += _parse_n(m.group("n"))

    for m in _THIS_TURN_RE.finditer(t):
        this_turn += _parse_n(m.group("n"))

    return each_turn, this_turn, saw


def extra_land_drops_from_board(st, idx) -> Tuple[int, bool]:
    """
    Returns (extra_each_turn, saw_unhandled_phrase).
    saw_unhandled_phrase is True if we saw 'additional land(s)' text we didn't match with a known template.
    """
    extra = 0
    saw_unhandled = False
    for p in st.iter_permanents():
        txt = idx.oracle_for_perm(p) or ""
        each_turn, _this_turn, saw = scan_extra_land_drops_from_text(txt)
        extra += each_turn
        if saw and each_turn == 0 and _this_turn == 0:
            saw_unhandled = True
    return extra, saw_unhandled
