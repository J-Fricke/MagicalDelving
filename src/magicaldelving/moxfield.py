from __future__ import annotations

import re
from typing import Any, Dict, Tuple

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


def _zone_to_counts(zone: Any) -> Dict[str, int]:
    """
    Moxfield zones are usually dict[str, {quantity: int, ...}].
    We also defensively handle a list-of-entries style.
    """
    out: Dict[str, int] = {}

    if isinstance(zone, dict):
        for name, entry in zone.items():
            if not name:
                continue
            qty = 1
            if isinstance(entry, dict):
                q = entry.get("quantity")
                if isinstance(q, int) and q > 0:
                    qty = q
            clean = (name or "").strip()
            if clean:
                out[clean] = out.get(clean, 0) + qty
        return out

    if isinstance(zone, list):
        for entry in zone:
            if not isinstance(entry, dict):
                continue
            # common shapes: {"card": {"name": ...}, "quantity": ...}
            name = None
            card = entry.get("card")
            if isinstance(card, dict):
                name = card.get("name")
            if not name:
                name = entry.get("name")
            qty = entry.get("quantity")
            if not isinstance(qty, int) or qty <= 0:
                qty = 1
            clean = (name or "").strip()
            if clean:
                out[clean] = out.get(clean, 0) + qty
        return out

    return out


def parse_deck_json(deck_json: Dict[str, Any]) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    Returns (commanders, mainboard) as name->qty dicts.
    """
    commanders = _zone_to_counts(deck_json.get("commanders") or deck_json.get("commander") or {})
    mainboard = _zone_to_counts(deck_json.get("mainboard") or {})

    # Some decks may redundantly include commanders in mainboard.
    # Prefer commander zone and remove duplicates from mainboard to avoid double-counting.
    for name, q in list(commanders.items()):
        if name in mainboard:
            mainboard[name] = max(0, mainboard[name] - q)
            if mainboard[name] <= 0:
                del mainboard[name]

    return commanders, mainboard


def parse_moxfield_json_to_cards(deck_json: Dict[str, Any]) -> Dict[str, int]:
    """
    Back-compat helper for topdeck-meta: returns mainboard-only name->qty.
    """
    _, mainboard = parse_deck_json(deck_json)
    return mainboard


def deck_json_to_deck_text(deck_json: Dict[str, Any]) -> str:
    """
    Converts Moxfield JSON into a deterministic decklist text format compatible with deck_parser.parse_deck_text.
    """
    commanders, mainboard = parse_deck_json(deck_json)

    lines = []
    lines.append("Commander")
    if not commanders:
        # Fall back to using "commanders" embedded in the title line if something is off,
        # but keep failure obvious to the parser.
        lines.append("1 UNKNOWN_COMMANDER")
    else:
        for name in sorted(commanders.keys(), key=str.lower):
            lines.append(f"{commanders[name]} {name}")

    lines.append("")
    lines.append("Mainboard")
    for name in sorted(mainboard.keys(), key=str.lower):
        lines.append(f"{mainboard[name]} {name}")

    return "\n".join(lines) + "\n"
