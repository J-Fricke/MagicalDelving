from __future__ import annotations

import re
from typing import Optional

from .index import CardIndex
from .models import GameState, Permanent

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

_UPKEEP = "at the beginning of your upkeep"
_FIRST_MAIN = "at the beginning of your first main phase"
_PRECOMBAT_MAIN = "at the beginning of your precombat main phase"
_END_STEP = "at the beginning of your end step"

_CONTROL_CREATURES_RE = re.compile(
    r"if\s+you\s+control\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+or\s+more\s+creatures",
    re.IGNORECASE,
)
_ATTACKED_RE = re.compile(
    r"if\s+you\s+attacked\s+with\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+or\s+more\s+creatures",
    re.IGNORECASE,
)

_COUNTER_ADD_RE = re.compile(
    r"at\s+the\s+beginning\s+of\s+your\s+end\s+step,\s+put\s+an?\s+([a-z0-9' -]+?)\s+counter\s+on\s+",
    re.IGNORECASE,
)
_COUNTER_XFORM_RE = re.compile(
    r"(?:then\s+)?if\s+.*?\s+has\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+or\s+more\s+([a-z0-9' -]+?)\s+counters?\s+on\s+it,\s+(?:transform|convert)\b",
    re.IGNORECASE | re.DOTALL,
    )


def _parse_n(s: str) -> Optional[int]:
    if not s:
        return None
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    return _WORD_NUM.get(s)


def _has_alt_face(idx: CardIndex, p: Permanent) -> bool:
    f = idx.facts(p.name)
    if not f:
        return False
    try:
        return f.num_faces() >= 2
    except Exception:
        return True  # safe fallback


def _toggle_face(p: Permanent) -> None:
    p.face = 1 if p.face == 0 else 0


def _total_creatures_controlled(st: GameState, idx: CardIndex) -> int:
    n = st.token_pool
    for perm in st.iter_permanents():
        if idx.is_creature_perm(perm):
            n += 1
    return n


def _matches_step(oracle_lc: str, step: str) -> bool:
    if step == "upkeep":
        return _UPKEEP in oracle_lc
    if step == "first_main":
        return (_FIRST_MAIN in oracle_lc) or (_PRECOMBAT_MAIN in oracle_lc)
    if step == "end_step":
        return _END_STEP in oracle_lc
    return False


def _should_transform(st: GameState, idx: CardIndex, p: Permanent, oracle_lc: str) -> bool:
    if ("transform" not in oracle_lc) and ("convert" not in oracle_lc):
        return False

    m = _COUNTER_XFORM_RE.search(oracle_lc)
    if m:
        thresh = _parse_n(m.group(1))
        cname = (m.group(2) or "").strip().lower()
        if thresh is not None and cname:
            if int(p.counters.get(cname, 0)) >= thresh:
                return True

    m = _CONTROL_CREATURES_RE.search(oracle_lc)
    if m:
        thresh = _parse_n(m.group(1))
        if thresh is not None and _total_creatures_controlled(st, idx) >= thresh:
            return True

    m = _ATTACKED_RE.search(oracle_lc)
    if m:
        thresh = _parse_n(m.group(1))
        if thresh is not None:
            attackers = getattr(st, "attackers_this_turn", 0)
            if ("last turn" in oracle_lc) or ("since your last turn" in oracle_lc):
                attackers = getattr(st, "attackers_last_turn", 0)
            if attackers >= thresh:
                return True

    return False


def apply_upkeep(st: GameState, idx: CardIndex) -> None:
    for p in st.iter_permanents():
        if not _has_alt_face(idx, p):
            continue
        oracle_lc = (idx.oracle_for_perm(p) or "").lower()
        if not _matches_step(oracle_lc, "upkeep"):
            continue
        if _should_transform(st, idx, p, oracle_lc):
            _toggle_face(p)


def apply_first_main(st: GameState, idx: CardIndex) -> None:
    for p in st.iter_permanents():
        if not _has_alt_face(idx, p):
            continue
        oracle_lc = (idx.oracle_for_perm(p) or "").lower()
        if not _matches_step(oracle_lc, "first_main"):
            continue
        if _should_transform(st, idx, p, oracle_lc):
            _toggle_face(p)


def apply_end_step(st: GameState, idx: CardIndex) -> None:
    # add counters first
    for p in st.iter_permanents():
        oracle_lc = (idx.oracle_for_perm(p) or "").lower()
        if _END_STEP not in oracle_lc:
            continue
        m = _COUNTER_ADD_RE.search(oracle_lc)
        if not m:
            continue
        cname = (m.group(1) or "").strip().lower()
        if cname:
            p.counters[cname] = int(p.counters.get(cname, 0)) + 1

    # then transform checks
    for p in st.iter_permanents():
        if not _has_alt_face(idx, p):
            continue
        oracle_lc = (idx.oracle_for_perm(p) or "").lower()
        if not _matches_step(oracle_lc, "end_step"):
            continue
        if _should_transform(st, idx, p, oracle_lc):
            _toggle_face(p)