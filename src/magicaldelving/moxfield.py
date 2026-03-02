from __future__ import annotations

import re
from typing import Any, Dict, Set, Tuple

import requests


_USER_AGENT = "MagicalDelving/0.1 (+https://github.com/J-Fricke/MagicalDelving)"


def deck_id_from_url(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    m = re.search(r"/decks/([A-Za-z0-9_-]+)", s)
    return m.group(1) if m else s


def fetch_deck_json_single_try(deck_url_or_id: str, timeout_s: int = 30) -> Dict[str, Any]:
    deck_id = deck_id_from_url(deck_url_or_id)
    url = f"https://api2.moxfield.com/v2/decks/all/{deck_id}"

    r = requests.get(url, timeout=timeout_s, headers={"User-Agent": _USER_AGENT})

    if r.status_code in (401, 403, 404):
        raise RuntimeError(
            f"Moxfield deck not accessible (HTTP {r.status_code}). "
            "If the deck is unlisted/private, change it to Public before fetching."
        )

    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("Moxfield returned non-object JSON.")
    return data


# ----------------------------
# Tags helpers
# ----------------------------

_DBL_SLASH_RE = re.compile(r"\s*//\s*")


def _clean_tag(s: str) -> str:
    s = (s or "").replace(",", " ").replace("[", " ").replace("]", " ").replace("(", " ").replace(")", " ")
    return re.sub(r"\s+", " ", s.strip())


def _mx_category_to_roles(cat: str) -> Set[str]:
    c = (cat or "").strip().lower()
    out: Set[str] = set()

    if any(k in c for k in ["ramp", "mana", "rock", "dork", "accelerat"]):
        out.add("Ramp")
    if any(k in c for k in ["draw", "card advantage", "advantage", "engine"]):
        out.add("DrawEngine")
    if any(k in c for k in ["wheel", "refill", "reload"]):
        out.add("Refill")
    # Wincon: only explicit
    if any(k in c for k in ["wincon", "combo"]):
        out.add("Wincon")
    # Finishers: not instant wins (Finale/Stampede live here)
    if any(k in c for k in ["finisher", "overrun", "anthem"]):
        out.add("Finisher")
    if any(k in c for k in ["evasion", "unblock", "flying", "menace", "trample"]):
        out.add("Evasion")
    if any(k in c for k in ["extra combat", "combat step"]):
        out.add("ExtraCombat")
    if any(k in c for k in ["threat", "damage", "beatdown", "pressure"]):
        out.add("Damage")
    if any(k in c for k in ["token", "tokens"]):
        out.add("TokenMaker")

    return out


def _extract_author_tags(deck_json: Dict[str, Any]) -> Dict[str, Set[str]]:
    """
    Moxfield deck JSON includes authorTags at the deck level:
      authorTags: { "<Card Name>": ["Ramp", "Draw", ...], ... }

    We convert these into:
      - raw tags: "Ramp"
      - plus moxfield-sourced tags: "Mx:Ramp" (so explain-roles can attribute)
      - plus normalized roles derived from each tag (Ramp/DrawEngine/etc.)
    """
    raw = deck_json.get("authorTags") or deck_json.get("author_tags") or {}
    out: Dict[str, Set[str]] = {}

    if not isinstance(raw, dict):
        return out

    for card_name, tags in raw.items():
        if not isinstance(card_name, str) or not card_name.strip():
            continue

        name = _clean_tag(card_name)
        tset: Set[str] = set()

        # tags may be list[str] (expected)
        if isinstance(tags, list):
            for t in tags:
                if isinstance(t, str) and t.strip():
                    ct = _clean_tag(t)
                    if ct:
                        tset.add(ct)
                        tset.add(f"Mx:{ct}")
                        tset |= _mx_category_to_roles(ct)
        # sometimes a single string
        elif isinstance(tags, str) and tags.strip():
            for t in tags.split(","):
                ct = _clean_tag(t)
                if ct:
                    tset.add(ct)
                    tset.add(f"Mx:{ct}")
                    tset |= _mx_category_to_roles(ct)

        if tset:
            out[name] = tset

    return out


def _entry_categories(entry: Any) -> Set[str]:
    """
    Best-effort: sometimes tags/categories are embedded per-entry too.
    We support a few common shapes defensively.
    """
    cats: Set[str] = set()
    if not isinstance(entry, dict):
        return cats

    for k in ("categories", "category", "tags", "customTags", "custom_tags"):
        v = entry.get(k)
        if isinstance(v, str) and v.strip():
            cats.add(_clean_tag(v))
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str) and x.strip():
                    cats.add(_clean_tag(x))

    # sometimes nested under entry["card"]
    card = entry.get("card")
    if isinstance(card, dict):
        v = card.get("tags")
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str) and x.strip():
                    cats.add(_clean_tag(x))

    return cats


def _zone_to_counts_and_tags(zone: Any) -> Tuple[Dict[str, int], Dict[str, Set[str]]]:
    counts: Dict[str, int] = {}
    tags: Dict[str, Set[str]] = {}

    def add(name: str, qty: int, entry: Any) -> None:
        clean = (name or "").strip()
        if not clean:
            return
        counts[clean] = counts.get(clean, 0) + qty

        cats = _entry_categories(entry)
        if cats:
            tset = tags.setdefault(clean, set())
            for c in cats:
                tset.add(c)
                tset.add(f"Mx:{c}")
                tset |= _mx_category_to_roles(c)

    if isinstance(zone, dict):
        for name, entry in zone.items():
            qty = 1
            if isinstance(entry, dict):
                q = entry.get("quantity")
                if isinstance(q, int) and q > 0:
                    qty = q
            add(name, qty, entry)
        return counts, tags

    if isinstance(zone, list):
        for entry in zone:
            if not isinstance(entry, dict):
                continue
            name = None
            card = entry.get("card")
            if isinstance(card, dict):
                name = card.get("name")
            if not name:
                name = entry.get("name")
            qty = entry.get("quantity")
            if not isinstance(qty, int) or qty <= 0:
                qty = 1
            add(str(name or "").strip(), qty, entry)
        return counts, tags

    return counts, tags


def parse_deck_json(deck_json: Dict[str, Any]) -> Tuple[Dict[str, int], Dict[str, int]]:
    commanders, _ = _zone_to_counts_and_tags(deck_json.get("commanders") or deck_json.get("commander") or {})
    mainboard, _ = _zone_to_counts_and_tags(deck_json.get("mainboard") or {})

    for name, q in list(commanders.items()):
        if name in mainboard:
            mainboard[name] = max(0, mainboard[name] - q)
            if mainboard[name] <= 0:
                del mainboard[name]

    return commanders, mainboard


def parse_deck_json_with_tags(deck_json: Dict[str, Any]) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, Set[str]]]:
    commanders, ctags = _zone_to_counts_and_tags(deck_json.get("commanders") or deck_json.get("commander") or {})
    mainboard, mtags = _zone_to_counts_and_tags(deck_json.get("mainboard") or {})

    for name, q in list(commanders.items()):
        if name in mainboard:
            mainboard[name] = max(0, mainboard[name] - q)
            if mainboard[name] <= 0:
                del mainboard[name]

    tags: Dict[str, Set[str]] = {}
    for m in (ctags, mtags):
        for n, ts in m.items():
            tags.setdefault(n, set()).update(ts)

    # IMPORTANT: merge deck-level author tags (this is what your screenshot is showing)
    author_tags = _extract_author_tags(deck_json)
    for n, ts in author_tags.items():
        tags.setdefault(n, set()).update(ts)

    return commanders, mainboard, tags


def parse_moxfield_json_to_cards(deck_json: Dict[str, Any]) -> Dict[str, int]:
    _, mainboard = parse_deck_json(deck_json)
    return mainboard


def deck_json_to_deck_text(deck_json: Dict[str, Any]) -> str:
    commanders, mainboard, tags = parse_deck_json_with_tags(deck_json)

    def fmt_line(qty: int, name: str, extra_tags: Set[str] | None = None) -> str:
        t = set(tags.get(name, set()))
        if extra_tags:
            t |= set(extra_tags)
        if t:
            inside = ",".join(sorted(t, key=str.lower))
            return f"{qty} {name} [{inside}]"
        return f"{qty} {name}"

    lines = []
    lines.append("Commander")
    if not commanders:
        lines.append("1 UNKNOWN_COMMANDER [Commander]")
    else:
        for name in sorted(commanders.keys(), key=str.lower):
            lines.append(fmt_line(commanders[name], name, extra_tags={"Commander"}))

    lines.append("")
    lines.append("Mainboard")
    for name in sorted(mainboard.keys(), key=str.lower):
        lines.append(fmt_line(mainboard[name], name))

    return "\n".join(lines) + "\n"