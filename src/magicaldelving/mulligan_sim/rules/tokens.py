# src/magicaldelving/mulligan_sim/rules/tokens.py
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..models import GameState, Permanent


class TokenResolutionError(RuntimeError):
    pass


#TODO CountExpr like env_target_player_creatures need to be refactored for the token-count resolver layer
# TODO: replace default opp curve with richer OppState model for above
# ---------------------------
# Data structures
# ---------------------------

@dataclass(frozen=True)
class TokenProto:
    name: str
    types: Set[str]
    subtypes: Set[str] = field(default_factory=set)
    power: Optional[int] = None
    toughness: Optional[int] = None
    keywords: Set[str] = field(default_factory=set)
    tapped: bool = False
    attacking: bool = False
    # if set, tokens should be removed at that timing (e.g. Mobilize)
    sacrifice_timing: Optional[str] = None  # "NEXT_END_STEP"


@dataclass(frozen=True)
class CountExpr:
    kind: str  # "fixed" | "chosen_x" | "soldiers_you_control" | "creatures_you_control" | "env_target_player_creatures" | "env_opponents_attacked"
    value: int = 0
    min_x: Optional[int] = None  # for conditional templates (e.g. X>=10)


@dataclass(frozen=True)
class TokenCreateTemplate:
    count: CountExpr
    proto: TokenProto


# ---------------------------
# Regex + keyword maps
# ---------------------------

_WORD_NUM: Dict[str, int] = {
    "a": 1, "an": 1,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
}

_COLOR_WORDS = {"white", "blue", "black", "red", "green", "colorless"}

_KW = {
    "flying": "Flying",
    "trample": "Trample",
    "haste": "Haste",
    "double strike": "DoubleStrike",
    "first strike": "FirstStrike",
    "vigilance": "Vigilance",
    "menace": "Menace",
    "deathtouch": "Deathtouch",
    "lifelink": "Lifelink",
    "indestructible": "Indestructible",
    "reach": "Reach",
}

# "Create X 1/1 white Soldier creature token(s) with flying and vigilance."
_CREATE_RE = re.compile(
    r"\bcreate\s+(?P<count>\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|x)\s+"
    r"(?P<body>[^\.]*?)\btoken\b",
    re.IGNORECASE,
)

_PT_RE = re.compile(r"(?P<p>\d+)\s*/\s*(?P<t>\d+)")

# Finale of Glory-style condition
_IF_X_GE_RE = re.compile(r"\bif\s+x\s+is\s+(?P<n>\d+)\s+or\s+more\b", re.IGNORECASE)

# "where X is the number of Soldiers you control" (very small initial set)
_WHERE_X_SOLDIERS_RE = re.compile(r"where\s+x\s+is\s+the\s+number\s+of\s+soldiers\s+you\s+control", re.IGNORECASE)
_WHERE_X_CREATURES_RE = re.compile(r"where\s+x\s+is\s+the\s+number\s+of\s+creatures\s+you\s+control", re.IGNORECASE)

# Mobilize N (Voice of Victory)
_MOBILIZE_RE = re.compile(r"\bmobilize\s+(?P<n>\d+)\b", re.IGNORECASE)


# ---------------------------
# Parsing
# ---------------------------

def _parse_count_token(raw: str) -> CountExpr:
    s = (raw or "").strip().lower()
    if s.isdigit():
        return CountExpr(kind="fixed", value=int(s))
    if s in _WORD_NUM:
        return CountExpr(kind="fixed", value=_WORD_NUM[s])
    if s == "x":
        return CountExpr(kind="chosen_x")
    return CountExpr(kind="fixed", value=1)


def _extract_keywords(text: str) -> Set[str]:
    low = (text or "").lower()
    out: Set[str] = set()
    for k, v in _KW.items():
        if f"with {k}" in low or f"have {k}" in low or f"gain {k}" in low:
            out.add(v)
    return out


def _parse_types_subtypes(desc: str) -> (Set[str], Set[str]):
    low = (desc or "").lower()
    words = [w for w in re.split(r"\s+", low) if w]

    types: Set[str] = set()
    subtypes: Set[str] = set()

    if "artifact" in words:
        types.add("Artifact")
    if "enchantment" in words:
        types.add("Enchantment")
    if "creature" in words:
        types.add("Creature")

    if "creature" in words:
        i = words.index("creature")
        for w in words[:i]:
            if w in _COLOR_WORDS:
                continue
            if w in {"artifact", "enchantment", "token"}:
                continue
            subtypes.add(w.capitalize())

    return types, subtypes


def parse_token_templates(oracle_text: str) -> List[TokenCreateTemplate]:
    """
    Parse token creation templates from a single oracle text blob.
    Deterministic resolution happens later (chosen_x/state/env).
    """
    txt = oracle_text or ""
    out: List[TokenCreateTemplate] = []

    # 1) Mobilize N (attack trigger) -> N 1/1 red Warrior tapped+attacking, sac next end step
    mob = _MOBILIZE_RE.search(txt)
    if mob:
        n = int(mob.group("n"))
        out.append(
            TokenCreateTemplate(
                count=CountExpr(kind="fixed", value=n),
                proto=TokenProto(
                    name="Warrior Token",
                    types={"Creature"},
                    subtypes={"Warrior"},
                    power=1,
                    toughness=1,
                    keywords=set(),
                    tapped=True,
                    attacking=True,
                    sacrifice_timing="NEXT_END_STEP",
                ),
            )
        )

    # 2) Standard "create ..." clauses (can be multiple)
    # Also capture Finale-like conditional templates by looking for "If X is N or more" in the same text.
    cond = _IF_X_GE_RE.search(txt)
    cond_min_x = int(cond.group("n")) if cond else None

    for m in _CREATE_RE.finditer(txt):
        cnt = _parse_count_token(m.group("count"))
        body = (m.group("body") or "")
        body_low = body.lower()

        tapped = "tapped" in body_low
        attacking = "attacking" in body_low

        # P/T if present
        pwr = tgh = None
        ptm = _PT_RE.search(body)
        if ptm:
            pwr = int(ptm.group("p"))
            tgh = int(ptm.group("t"))

        # If there's a "where X is ..." in the text, specialize the chosen_x expr
        if cnt.kind == "chosen_x":
            if _WHERE_X_SOLDIERS_RE.search(txt):
                cnt = CountExpr(kind="soldiers_you_control")
            elif _WHERE_X_CREATURES_RE.search(txt):
                cnt = CountExpr(kind="creatures_you_control")

        types, subtypes = _parse_types_subtypes(body[ptm.end():] if ptm else body)
        kws = _extract_keywords(body)

        out.append(
            TokenCreateTemplate(
                count=cnt,
                proto=TokenProto(
                    name="Creature Token",
                    types=types if types else {"Creature"},
                    subtypes=subtypes,
                    power=pwr,
                    toughness=tgh,
                    keywords=kws,
                    tapped=tapped,
                    attacking=attacking,
                ),
            )
        )

    # 3) Finale of Glory special: the second token line is conditional on X>=10.
    # We can represent this by marking min_x on templates that are "chosen_x" and have Angels/4/4 etc.
    # To keep this file generic, we just attach min_x to *all* chosen_x templates if the card has that clause.
    # (Call site can refine by card name if you want later.)
    if cond_min_x is not None:
        new_out: List[TokenCreateTemplate] = []
        for t in out:
            if t.count.kind == "chosen_x":
                new_out.append(TokenCreateTemplate(count=CountExpr(kind="chosen_x", min_x=cond_min_x), proto=t.proto))
            else:
                new_out.append(t)
        out = new_out

    return out


# ---------------------------
# Resolution (no guessing)
# ---------------------------

def _count_creatures(st: GameState) -> int:
    return sum(p.qty for p in st.iter_permanents() if p.is_creature())


def _count_soldiers(st: GameState) -> int:
    return sum(p.qty for p in st.iter_permanents() if p.is_creature() and ("Soldier" in p.subtypes))


def resolve_count(expr: CountExpr, st: GameState, *, chosen_x: Optional[int] = None, env: Optional[dict] = None) -> int:
    env = env or {}

    if expr.kind == "fixed":
        return max(0, expr.value)

    if expr.kind == "chosen_x":
        if chosen_x is None:
            raise TokenResolutionError("Token count needs chosen_x but none was provided.")
        if expr.min_x is not None and chosen_x < expr.min_x:
            return 0
        return max(0, int(chosen_x))

    if expr.kind == "creatures_you_control":
        return max(0, _count_creatures(st))

    if expr.kind == "soldiers_you_control":
        return max(0, _count_soldiers(st))

    if expr.kind == "env_target_player_creatures":
        if "target_player_creatures" not in env:
            raise TokenResolutionError("Token count needs env.target_player_creatures.")
        return max(0, int(env["target_player_creatures"]))

    if expr.kind == "env_opponents_attacked":
        if "opponents_attacked" not in env:
            raise TokenResolutionError("Token count needs env.opponents_attacked (usually 3).")
        return max(0, int(env["opponents_attacked"]))

    raise TokenResolutionError(f"Unknown CountExpr kind: {expr.kind}")


def resolve_templates(
        templates: List[TokenCreateTemplate],
        st: GameState,
        *,
        chosen_x: Optional[int] = None,
        env: Optional[dict] = None,
) -> List[TokenProto]:
    """
    Resolve templates into concrete token creations. Returns a flat list of TokenProto instances with qty applied via repetition.
    Call site can instead return (proto, qty) if preferred; kept simple here.
    """
    out: List[TokenProto] = []
    for t in templates:
        n = resolve_count(t.count, st, chosen_x=chosen_x, env=env)
        if n <= 0:
            continue
        # Repeat proto n times (callers can compress to qty later)
        out.extend([t.proto] * n)
    return out
