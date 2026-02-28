from __future__ import annotations

import re
from typing import Any, Dict, Set, Tuple

import requests


_USER_AGENT = "MagicalDelving/0.1 (+https://github.com/J-Fricke/MagicalDelving)"


def deck_id_from_url(url_or_id: str) -> str:
    """
    Accepts a raw Moxfield deck id or a URL containing /decks/<id>.
    Returns the id string.
    """
    s = (url_or_id or "").strip()
    m = re.search(r"/decks/([A-Za-z0-9_-]+)", s)
    return m.group(1) if m else s


def fetch_deck_json_single_try(deck_url_or_id: str, timeout_s: int = 30) -> Dict[str, Any]:
    """
    Fetches the full deck JSON from Moxfield (single try, no retries).
    Raises a clear error for inaccessible decks (private/unlisted).
    """
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
# Categories / tags
# ----------------------------

def _clean_tag(s: str) -> str:
    # Keep tags readable, but consistent, and safe for comma-separated bracket tags.
    s = (s or "").replace(",", " ").replace("[", " ").replace("]", " ").replace("(", " ").replace(")", " ")
    return re.sub(r"\s+", " ", s.strip())


def _mx_category_to_roles(cat: str) -> Set[str]:
    """
    Map common Moxfield category names (or user-defined categories) into the small
    role vocabulary used by mulligan_sim.
    """
    c = (cat or "").strip().lower()
    out: Set[str] = set()

    # Ramp
    if any(k in c for k in ["ramp", "mana", "rock", "dork", "accelerat"]):
        out.add("Ramp")

    # Draw engines / refills
    if any(k in c for k in ["draw", "card advantage", "advantage", "engine"]):
        out.add("DrawEngine")
    if any(k in c for k in ["wheel", "refill", "reload"]):
        out.add("Refill")

    # Wincons
    if any(k in c for k in ["win", "combo", "finisher", "payoff", "wincon"]):
        out.add("Wincon")

    # Combat heuristics
    if any(k in c for k in ["evasion", "unblock", "flying", "menace", "trample"]):
        out.add("Evasion")
    if any(k in c for k in ["extra combat", "combat step"]):
        out.add("ExtraCombat")
    if any(k in c for k in ["threat", "damage", "beatdown", "pressure"]):
        out.add("Damage")

    return out


def _entry_categories(entry: Any) -> Set[str]:
    """
    Best-effort extraction of Moxfield categories from a card entry.
    Common shapes:
      - entry["categories"] = ["Ramp", "Draw"]
      - entry["category"] = "Ramp"
      - entry["tags"] = [...]
    """
    cats: Set[str] = set()
    if not isinstance(entry, dict):
        return cats

    for k in ("categories", "category", "tags"):
        v = entry.get(k)
        if isinstance(v, str) and v.strip():
            cats.add(_clean_tag(v))
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str) and x.strip():
                    cats.add(_clean_tag(x))
    return cats


def _zone_to_counts_and_tags(zone: Any) -> Tuple[Dict[str, int], Dict[str, Set[str]]]:
    """
    Moxfield zones are usually dict[str, {quantity: int, ...}].
    We also defensively handle a list-of-entries style.
    Returns:
      - counts[name] = qty
      - tags[name] = set(tags)
    """
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
            # Keep raw categories (namespaced) for future use + add normalized sim roles.
            for c in cats:
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
    """
    Returns (commanders, mainboard) as name->qty dicts.
    """
    commanders, _ = _zone_to_counts_and_tags(deck_json.get("commanders") or deck_json.get("commander") or {})
    mainboard, _ = _zone_to_counts_and_tags(deck_json.get("mainboard") or {})

    # Some decks may redundantly include commanders in mainboard.
    # Prefer commander zone and remove duplicates from mainboard to avoid double-counting.
    for name, q in list(commanders.items()):
        if name in mainboard:
            mainboard[name] = max(0, mainboard[name] - q)
            if mainboard[name] <= 0:
                del mainboard[name]

    return commanders, mainboard


def parse_deck_json_with_tags(deck_json: Dict[str, Any]) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, Set[str]]]:
    """
    Returns (commanders, mainboard, tags_by_cardname).
    tags_by_cardname merges commander+mainboard tags (for any cards present),
    and includes normalized role tags derived from Moxfield categories.
    """
    commanders, ctags = _zone_to_counts_and_tags(deck_json.get("commanders") or deck_json.get("commander") or {})
    mainboard, mtags = _zone_to_counts_and_tags(deck_json.get("mainboard") or {})

    # De-dupe commander copies if they appear in mainboard
    for name, q in list(commanders.items()):
        if name in mainboard:
            mainboard[name] = max(0, mainboard[name] - q)
            if mainboard[name] <= 0:
                del mainboard[name]

    tags: Dict[str, Set[str]] = {}
    for m in (ctags, mtags):
        for n, ts in m.items():
            tags.setdefault(n, set()).update(ts)
    return commanders, mainboard, tags


def parse_moxfield_json_to_cards(deck_json: Dict[str, Any]) -> Dict[str, int]:
    """
    Back-compat helper for topdeck-meta: returns mainboard-only name->qty.
    """
    _, mainboard = parse_deck_json(deck_json)
    return mainboard


def deck_json_to_deck_text(deck_json: Dict[str, Any]) -> str:
    """
    Converts Moxfield JSON into a deterministic decklist text format compatible with deck_parser.parse_deck_text.

    Includes Moxfield categories as tags, plus normalized mulligan_sim roles:
      - raw categories become:   Mx:<Category>
      - roles become:           Ramp / DrawEngine / Refill / Wincon / Damage / Evasion / ExtraCombat
    """
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
            # Ensure commander tag is present for downstream parsing
            lines.append(fmt_line(commanders[name], name, extra_tags={"Commander"}))

    lines.append("")
    lines.append("Mainboard")
    for name in sorted(mainboard.keys(), key=str.lower):
        lines.append(fmt_line(mainboard[name], name))

    return "\n".join(lines) + "\n"