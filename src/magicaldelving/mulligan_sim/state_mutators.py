from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .index import CardIndex
from .models import GameState, Permanent


_ANTHEM_PT_RE = re.compile(r"creatures you control get \+(\d+)\/\+(\d+)", re.IGNORECASE)
_ANTHEM_TOK_RE = re.compile(r"creature tokens you control get \+(\d+)\/\+(\d+)", re.IGNORECASE)
_GRANT_HAVE_RE = re.compile(r"creatures you control have ([a-z ]+?)(?:\.|,|;|$)", re.IGNORECASE)

_KEYWORDS = {
    "flying": "Flying",
    "trample": "Trample",
    "haste": "Haste",
    "double strike": "DoubleStrike",
    "vigilance": "Vigilance",
    "menace": "Menace",
    "first strike": "FirstStrike",
    "deathtouch": "Deathtouch",
    "lifelink": "Lifelink",
    "indestructible": "Indestructible",
}


def recompute_continuous_effects(st: GameState, idx: CardIndex) -> None:
    # 1) reset derived stats
    for p in st.iter_permanents():
        # start from base (default to 0 if unknown)
        p.power = int(p.base_power) if p.base_power is not None else 0
        p.toughness = int(p.base_toughness) if p.base_toughness is not None else 0

        # (optional) counters support
        n = int(p.counters.get("+1/+1", 0) or 0)
        if n:
            p.power += n
            p.toughness += n

        # apply explicit override last
        if p.attack_power_override_this_turn is not None:
            try:
                p.power = max(0, int(p.attack_power_override_this_turn))
            except Exception:
                pass

    # 2) gather effects
    team_pt: List[Tuple[int, int]] = []        # (+P, +T) for creatures you control (single-player -> always applies)
    token_pt: List[Tuple[int, int]] = []       # (+P, +T) for creature tokens you control
    team_kw: List[str] = []

    for src in st.iter_permanents():
        txt = (idx.oracle_for_perm(src) or "").lower()

        m = _ANTHEM_PT_RE.search(txt)
        if m:
            team_pt.append((int(m.group(1)), int(m.group(2))))

        m = _ANTHEM_TOK_RE.search(txt)
        if m:
            token_pt.append((int(m.group(1)), int(m.group(2))))

        for mm in _GRANT_HAVE_RE.finditer(txt):
            kw_raw = mm.group(1).strip()
            if kw_raw in _KEYWORDS:
                team_kw.append(_KEYWORDS[kw_raw])

    # 3) apply effects
    for p in st.iter_permanents():
        if not p.is_creature():
            continue

        for dp, dt in team_pt:
            p.power = (p.power or 0) + dp
            p.toughness = (p.toughness or 0) + dt

        for kw in team_kw:
            p.keywords.add(kw)

        if not p.is_card:
            for dp, dt in token_pt:
                p.power = (p.power or 0) + dp
                p.toughness = (p.toughness or 0) + dt


def run_cleanup(st: GameState, idx: CardIndex) -> None:
    # apply delayed effects
    for pid, p in list(st.battlefield.items()):
        remaining = []
        for d in p.delayed:
            if d.timing == "CLEANUP":
                d.fn(st, pid)
            else:
                remaining.append(d)
        p.delayed = remaining

    st.emit("CLEANUP", {})
    recompute_continuous_effects(st, idx)
    merge_identical(st)


def merge_identical(st: GameState) -> None:
    groups: Dict[Tuple, int] = {}
    to_delete: List[int] = []

    for pid, p in st.battlefield.items():
        sig = p.merge_signature()
        if sig not in groups:
            groups[sig] = pid
        else:
            keep = groups[sig]
            st.battlefield[keep].qty += p.qty
            to_delete.append(pid)

    for pid in to_delete:
        del st.battlefield[pid]
