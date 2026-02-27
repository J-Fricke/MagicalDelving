# deck_parser.py
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

@dataclass
class Deck:
    library: List[str]               # expanded list length == 99 (single commander) or 98 (partners)
    commanders: List[str]            # length 1 or 2
    counts: Dict[str, int]           # name -> count (includes commander copies)
    inline_tags: Dict[str, Set[str]] # parsed from "(...)" and trailing "[...]"
    name: str = "DECK"

_LINE_RE = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")
_SECTION_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 /&'\-:,]*$")

def _split_name_and_paren_tags(rest: str) -> Tuple[str, Set[str]]:
    """
    If line ends with "(...)" we parse tags from inside.
    Card name is everything before first '(' with trailing spaces stripped.
    """
    rest = rest.strip()
    if "(" in rest and rest.endswith(")"):
        base, paren = rest.split("(", 1)
        name = base.rstrip()
        inside = paren[:-1].strip()
        tags = {t.strip() for t in inside.split(",") if t.strip()} if inside else set()
        return name, tags
    return rest, set()

_BRACKET_TAIL_RE = re.compile(r"^(.*?)(\s*\[([^\]]+)\]\s*)$")

def _split_trailing_bracket_tags(name: str) -> Tuple[str, Set[str]]:
    """
    Supports trailing bracket annotations like:
      "Card Name [Land]"
      "Card Name [Commander{top}]"
      "Card Name [Ramp,Draw]"
    Returns (clean_name, tags).
    """
    name = name.strip()
    m = _BRACKET_TAIL_RE.match(name)
    if not m:
        return name, set()

    base = m.group(1).rstrip()
    inside = (m.group(3) or "").strip()
    if not inside:
        return base, set()

    raw_tags = [t.strip() for t in inside.split(",") if t.strip()]
    tags: Set[str] = set()
    for t in raw_tags:
        # normalize things like "Commander{top}" -> "Commander"
        t = t.split("{", 1)[0].strip()
        if t:
            tags.add(t)
    return base, tags

def _is_section_header(line: str) -> bool:
    # A header is a non-qty line that looks like a word/phrase.
    return bool(line) and (_LINE_RE.match(line) is None) and bool(_SECTION_RE.match(line))

def parse_deck_text(deck_text: str, deck_name: str = "DECK") -> Deck:
    counts: Dict[str, int] = {}
    inline_tags: Dict[str, Set[str]] = {}
    commanders: List[str] = []

    section: str | None = None

    for raw in deck_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue

        if _is_section_header(line):
            section = line.strip().lower()
            continue

        m = _LINE_RE.match(line)
        if not m:
            # ignore other non-card lines (notes, separators, etc.)
            continue

        qty = int(m.group(1))
        rest = m.group(2).strip()

        # 1) parse paren tags
        name, tags = _split_name_and_paren_tags(rest)

        # 2) parse trailing bracket tags
        name, btags = _split_trailing_bracket_tags(name)
        tags.update(btags)

        counts[name] = counts.get(name, 0) + qty
        inline_tags.setdefault(name, set()).update(tags)

        in_commander_section = (section == "commander")
        is_commander_tagged = ("Commander" in tags)
        if in_commander_section or is_commander_tagged:
            commanders.extend([name] * qty)

    total_cards = sum(counts.values())
    if total_cards != 100:
        raise ValueError(f"Deck size != 100 (got {total_cards})")

    if len(commanders) not in (1, 2):
        raise ValueError(f"Expected 1 or 2 commanders, got {len(commanders)}")

    # Build library by excluding commander copies
    lib_counts = dict(counts)
    for c in commanders:
        if c not in lib_counts:
            raise ValueError(f"Commander '{c}' not found in counts")
        lib_counts[c] -= 1
        if lib_counts[c] <= 0:
            del lib_counts[c]

    library: List[str] = []
    for name, qty in lib_counts.items():
        library.extend([name] * qty)

    expected_lib = 100 - len(commanders)
    if len(library) != expected_lib:
        raise ValueError(f"Library size != {expected_lib} (got {len(library)})")

    return Deck(library=library, commanders=commanders, counts=counts, inline_tags=inline_tags, name=deck_name)