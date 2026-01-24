# ----------------------------
# Defaults (edit these easily)
# ----------------------------
DEFAULTS = {
    "base_url": "https://topdeck.gg/api/v2/tournaments",
    "api_key_env": "TOPDECK_API_KEY",

    # NOTE: TopDeck "last" is DAYS back from today, not number of tournaments.
    "last": 90,
    "participant_min": 50,

    "game": "Magic: The Gathering",
    "format": "EDH",

    "discover_from": "topY",              # first | topY | rest | all
    "topY": 16,

    # Back-compat: these names remain, but semantics are "excluding BEST" now.
    "compare": "topY_excluding_first",    # topY_excluding_first | topY_excluding_best | rest | all_non_first | all_non_best

    # Default show list size = Top16
    "show": 16,                           # commander sets shown in selection list

    # IMPORTANT: include "decklist" so the API can return deckObj when available
    "columns": ["decklist"],

    # Retry behavior for Cloudflare/origin hiccups (TopDeck)
    "max_retries": 8,
    "timeout_s": 90,

    # Moxfield request timeout (single try, no retries)
    "moxfield_timeout_s": 30,

    # ----------------------------
    # Tag thresholds (tune later)
    # ----------------------------
    # Core: very common in best decks
    "core_best_pct": 80.0,

    # ADD: missing from your deck AND strongly favored in best decks
    "add_best_pct_min": 60.0,
    "add_diff_min": 20.0,

    # MAYBE_ADD: missing AND mildly favored in best decks
    "maybe_add_best_pct_min": 40.0,
    "maybe_add_diff_min": 10.0,

    # SPICY: missing from your deck AND shows up in winners sometimes, but not enough to be MAYBE_ADD
    "spicy_best_pct_min": 15.0,
    "spicy_diff_min": 20.0,

    # CUT: in your deck AND strongly disfavored in best decks AND common in comparison group
    "cut_diff_max": -20.0,
    "cut_comp_pct_min": 40.0,

    # MAYBE_CUT: in your deck AND mildly disfavored in best decks AND common-ish in comparison group
    "maybe_cut_diff_max": -10.0,
    "maybe_cut_comp_pct_min": 40.0,
}


import os
import sys
import json
import time
import random
import argparse
import requests
import re
from collections import Counter
from typing import Any, Dict, List, Tuple, Optional, Set


# ----------------------------
# Utility helpers
# ----------------------------
def norm(s: str) -> str:
    return (s or "").strip()


def median_int(values: List[int]) -> Optional[int]:
    if not values:
        return None
    v = sorted(values)
    return v[len(v) // 2]


ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",

    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",

    "gray": "\033[90m",   # dim-ish gray
}


def ansi_enabled() -> bool:
    """
    Safe auto-detection:
      - Enable ANSI when stdout is a TTY (real terminal)
      - Otherwise, only enable in some JetBrains environments when TERM isn't dumb.
    If we're not confident, return False (so we don't spam escape codes).
    """
    try:
        if getattr(sys.stdout, "isatty", lambda: False)():
            return True
    except Exception:
        pass

    term = (os.environ.get("TERM") or "").lower()
    if not term or term == "dumb":
        return False

    for k in ("PYCHARM_HOSTED", "IDEA_INITIAL_DIRECTORY", "JETBRAINS_IDE", "TERMINAL_EMULATOR"):
        if k in os.environ:
            return True

    return False


def wrap(text: str, codes: List[str], enabled: bool) -> str:
    if not enabled or not codes:
        return text
    prefix = "".join(ANSI[c] for c in codes if c in ANSI)
    return f"{prefix}{text}{ANSI['reset']}"


# ----------------------------
# CLI / config
# ----------------------------
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()

    ap.add_argument("--base-url", default=DEFAULTS["base_url"])
    ap.add_argument("--api-key-env", default=DEFAULTS["api_key_env"])

    ap.add_argument(
        "--last",
        type=int,
        default=DEFAULTS["last"],
        help="Number of DAYS back from today to include tournaments (TopDeck 'last' param).",
    )
    ap.add_argument("--participant-min", type=int, default=DEFAULTS["participant_min"])

    ap.add_argument("--game", default=DEFAULTS["game"])
    ap.add_argument("--format", default=DEFAULTS["format"])

    ap.add_argument("--discover-from", choices=["first", "topY", "rest", "all"], default=DEFAULTS["discover_from"])
    ap.add_argument("--topY", type=int, default=DEFAULTS["topY"])

    ap.add_argument(
        "--compare",
        choices=[
            "topY_excluding_first",  # (alias) == topY_excluding_best
            "topY_excluding_best",
            "rest",
            "all_non_first",         # (alias) == all_non_best
            "all_non_best",
        ],
        default=DEFAULTS["compare"],
    )
    ap.add_argument("--show", type=int, default=DEFAULTS["show"])

    ap.add_argument("--save-json", default=None)

    ap.add_argument(
        "--commander",
        default=None,
        help='Optional commander set, e.g. "Tymna the Weaver|Dargo, the Shipwrecker" or "A // B"',
    )

    ap.add_argument("--no-sanity", action="store_true", help="Disable schema sanity prints.")

    ap.add_argument(
        "--moxfield",
        default=None,
        help='Moxfield deck URL or deck id (e.g. https://moxfield.com/decks/JUaSlpi5W0qmqYpHaqkOEA or JUaSlpi5W0qmqYpHaqkOEA)',
    )

    return ap.parse_args()


def build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "last": args.last,
        "game": args.game,
        "format": args.format,
        "participantMin": args.participant_min,
        "columns": DEFAULTS["columns"],
    }


# ----------------------------
# TopDeck fetch
# ----------------------------
def fetch_tournaments(base_url: str, api_key: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    headers = {
        "Authorization": api_key,  # if you get 401 later, try: f"Bearer {api_key}"
        "Content-Type": "application/json",
        "User-Agent": "MagicalDelving/0.1 (+local script)",
    }

    backoff = 1.0
    for attempt in range(DEFAULTS["max_retries"]):
        try:
            r = requests.post(base_url, json=payload, headers=headers, timeout=DEFAULTS["timeout_s"])

            if r.status_code in (429, 502, 503, 504, 520, 521, 522, 523, 524):
                sleep_s = backoff + random.uniform(0, 0.5)
                print(f"HTTP {r.status_code} from upstream, retrying in {sleep_s:.1f}s...")
                time.sleep(sleep_s)
                backoff = min(backoff * 2, 30)
                continue

            if r.status_code != 200:
                raise RuntimeError(f"Fetch failed: {r.status_code} {r.text[:400]}")

            data = r.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                tournaments = data.get("tournaments") or data.get("data")
                if isinstance(tournaments, list):
                    return tournaments

            raise ValueError("Unexpected response JSON shape.")

        except requests.RequestException as e:
            sleep_s = backoff + random.uniform(0, 0.5)
            print(f"Network error {e}, retrying in {sleep_s:.1f}s...")
            time.sleep(sleep_s)
            backoff = min(backoff * 2, 30)

    raise RuntimeError("Failed after retries; TopDeck/Cloudflare may be down or request too heavy.")


# ----------------------------
# Tournament parsing / selection
# ----------------------------
def decks_by_tier(t: Dict[str, Any], top_y: int = 16) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    standings = t.get("standings", []) or []
    entries = [e for e in standings if isinstance(e, dict) and e.get("deckObj")]

    first = [entries[0]["deckObj"]] if entries else []
    topy = [e["deckObj"] for e in entries[:top_y]]
    rest = [e["deckObj"] for e in entries[top_y:]]
    return first, topy, rest


def commanders_from_deck(deck: Dict[str, Any]) -> List[str]:
    cmds = deck.get("Commanders", {}) or {}
    if isinstance(cmds, dict):
        return [norm(k) for k in cmds.keys() if norm(k)]
    if isinstance(cmds, list):
        return [norm(x) for x in cmds if norm(x)]
    return []


def commander_key(cmds: List[str]) -> str:
    return " // ".join(sorted([norm(c) for c in cmds if norm(c)]))


def discover_commander_sets(tournaments: List[Dict[str, Any]], discover_from: str, top_y: int) -> Counter:
    counts = Counter()
    for t in tournaments:
        first, topy, rest = decks_by_tier(t, top_y=top_y)

        if discover_from == "first":
            decks = first
        elif discover_from == "topY":
            decks = topy
        elif discover_from == "rest":
            decks = rest
        else:
            decks = topy + rest

        for d in decks:
            ck = commander_key(commanders_from_deck(d))
            if ck:
                counts[ck] += 1
    return counts


def prompt_select_commander_set(options: List[Tuple[str, int]], allow_custom: bool = True) -> str:
    print("\nSelect a commander set to analyze:")
    for i, (k, c) in enumerate(options, start=1):
        print(f"{i:2d}) {k}  ({c})")

    if allow_custom:
        print("\nOr type your own commander(s) separated by | (pipe). Example: Tymna the Weaver|Dargo, the Shipwrecker")

    while True:
        raw = input("\nChoice #: ").strip()
        if allow_custom and raw and (not raw.isdigit()):
            parts = [norm(p) for p in raw.split("|") if norm(p)]
            if not parts:
                print("Could not parse commanders. Try again.")
                continue
            return commander_key(parts)

        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1][0]
            print("Out of range. Try again.")
            continue

        print("Invalid input. Enter a number from the list, or type commanders separated by |.")


def deck_mainboard(deck: Dict[str, Any]) -> Dict[str, Any]:
    return deck.get("Mainboard", {}) or {}


def inclusion_counts_for_commander(decks: List[Dict[str, Any]], target_key: str) -> Tuple[int, Counter]:
    n = 0
    counter = Counter()
    for d in decks:
        if commander_key(commanders_from_deck(d)) != target_key:
            continue
        n += 1
        for card in deck_mainboard(d).keys():
            counter[norm(card)] += 1
    return n, counter


def _normalize_compare_mode(compare: str) -> str:
    if compare == "topY_excluding_first":
        return "topY_excluding_best"
    if compare == "all_non_first":
        return "all_non_best"
    return compare


def best_deck_for_commander_in_tournament(
        t: Dict[str, Any],
        target_key: str
) -> Tuple[Optional[Dict[str, Any]], Optional[int], List[Dict[str, Any]]]:
    standings = t.get("standings", []) or []
    entries = [e for e in standings if isinstance(e, dict) and e.get("deckObj")]
    decks_in_order = [e["deckObj"] for e in entries]

    target_decks_in_order: List[Dict[str, Any]] = []
    best_deck: Optional[Dict[str, Any]] = None
    best_rank: Optional[int] = None

    for idx, deck in enumerate(decks_in_order):
        if commander_key(commanders_from_deck(deck)) == target_key:
            target_decks_in_order.append(deck)
            if best_deck is None:
                best_deck = deck
                best_rank = idx + 1

    return best_deck, best_rank, target_decks_in_order


def run_best_vs_compare(
        tournaments: List[Dict[str, Any]],
        target_key: str,
        compare: str,
        top_y: int
) -> Tuple[int, Counter, int, Counter, List[int]]:
    compare_mode = _normalize_compare_mode(compare)

    best_decks: List[Dict[str, Any]] = []
    best_ranks: List[int] = []
    compare_pool: List[Dict[str, Any]] = []

    for t in tournaments:
        best_deck, best_rank, target_decks_in_order = best_deck_for_commander_in_tournament(t, target_key)
        if not best_deck or best_rank is None:
            continue

        best_decks.append(best_deck)
        best_ranks.append(best_rank)

        others = [d for d in target_decks_in_order if d is not best_deck]

        if compare_mode == "all_non_best":
            compare_pool.extend(others)

        elif compare_mode == "rest":
            standings = t.get("standings", []) or []
            entries = [e for e in standings if isinstance(e, dict) and e.get("deckObj")]
            deck_order = [e["deckObj"] for e in entries]
            rest_region = set(id(d) for d in deck_order[top_y:])
            compare_pool.extend([d for d in others if id(d) in rest_region])

        else:
            standings = t.get("standings", []) or []
            entries = [e for e in standings if isinstance(e, dict) and e.get("deckObj")]
            deck_order = [e["deckObj"] for e in entries]
            topy_region = set(id(d) for d in deck_order[:top_y])
            compare_pool.extend([d for d in others if id(d) in topy_region])

    best_n, best_counts = inclusion_counts_for_commander(best_decks, target_key)
    comp_n, comp_counts = inclusion_counts_for_commander(compare_pool, target_key)
    return best_n, best_counts, comp_n, comp_counts, best_ranks


# ----------------------------
# Moxfield: single-try fetch + visibility gate
# ----------------------------
def moxfield_deck_id_from_url(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    m = re.search(r"/decks/([A-Za-z0-9_-]+)", s)
    return m.group(1) if m else s


def fetch_moxfield_deck_json_single_try(deck_url_or_id: str, timeout_s: int = 30) -> Dict[str, Any]:
    deck_id = moxfield_deck_id_from_url(deck_url_or_id)
    url = f"https://api2.moxfield.com/v2/decks/all/{deck_id}"

    r = requests.get(url, timeout=timeout_s, headers={"User-Agent": "MagicalDelving/0.1 (+local script)"})

    if r.status_code in (401, 403, 404):
        raise RuntimeError(
            f"Moxfield deck not accessible (HTTP {r.status_code}). "
            "If the deck is unlisted/private, change it to Public before comparison will work."
        )

    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("Moxfield returned non-object JSON.")
    return data


def parse_moxfield_json_to_cards(deck_json: Dict[str, Any]) -> Dict[str, int]:
    cards: Dict[str, int] = {}
    mainboard = deck_json.get("mainboard") or {}
    if isinstance(mainboard, dict):
        for name, entry in mainboard.items():
            if not name:
                continue
            qty = 1
            if isinstance(entry, dict):
                q = entry.get("quantity")
                if isinstance(q, int) and q > 0:
                    qty = q
            cards[norm(name)] = cards.get(norm(name), 0) + qty
    return cards


# ----------------------------
# Tagging + printing
# ----------------------------
def compute_tag(in_deck: bool, best_pct: float, comp_pct: float, diff: float) -> str:
    core_best_pct = DEFAULTS["core_best_pct"]

    add_best_min = DEFAULTS["add_best_pct_min"]
    add_diff_min = DEFAULTS["add_diff_min"]

    maybe_add_best_min = DEFAULTS["maybe_add_best_pct_min"]
    maybe_add_diff_min = DEFAULTS["maybe_add_diff_min"]

    spicy_best_min = DEFAULTS["spicy_best_pct_min"]
    spicy_diff_min = DEFAULTS["spicy_diff_min"]

    cut_diff_max = DEFAULTS["cut_diff_max"]
    cut_comp_min = DEFAULTS["cut_comp_pct_min"]

    maybe_cut_diff_max = DEFAULTS["maybe_cut_diff_max"]
    maybe_cut_comp_min = DEFAULTS["maybe_cut_comp_pct_min"]

    # CORE and CORE_MISSING (yellow for missing)
    if best_pct >= core_best_pct:
        return "CORE" if in_deck else "CORE_MISSING"

    # CUT / MAYBE_CUT
    if in_deck and diff <= cut_diff_max and comp_pct >= cut_comp_min:
        return "CUT"
    if in_deck and diff <= maybe_cut_diff_max and comp_pct >= maybe_cut_comp_min:
        return "MAYBE_CUT"

    # ADD / MAYBE_ADD
    if (not in_deck) and best_pct >= add_best_min and diff >= add_diff_min:
        return "ADD"
    if (not in_deck) and best_pct >= maybe_add_best_min and diff >= maybe_add_diff_min:
        return "MAYBE_ADD"

    # SPICY tech: big diff, some best presence, but not enough for MAYBE_ADD
    if (not in_deck) and diff >= spicy_diff_min and best_pct >= spicy_best_min:
        return "SPICY"

    # WATCH: present in best decks, but not enough to recommend (and not spicy/add)
    if (not in_deck) and best_pct > 0:
        return "WATCH"

    # Baselines to avoid blank tags
    if in_deck:
        return "KEEP"
    if comp_pct > 0:
        return "COMP_ONLY"
    return "OTHER"


def tag_style(tag: str) -> List[str]:
    """
    Color scheme:
    - Yellow = CORE_MISSING
    - Red/Magenta = CUT/MAYBE_CUT
    - Green/Cyan = ADD/MAYBE_ADD
    - Blue = SPICY
    - Gray = WATCH (info)
    - CORE = gray (low-salience)
    - KEEP/COMP_ONLY/OTHER = uncolored (still tagged, but not noisy)
    """
    if tag == "CORE_MISSING":
        return ["yellow"]
    if tag == "CUT":
        return ["red"]
    if tag == "MAYBE_CUT":
        return ["magenta"]
    if tag == "ADD":
        return ["green"]
    if tag == "MAYBE_ADD":
        return ["cyan"]
    if tag == "SPICY":
        return ["blue"]
    if tag == "WATCH":
        return ["gray"]
    if tag == "CORE":
        return ["gray"]
    return []


def print_tag_legend(ansi_on: bool) -> None:
    if not ansi_on:
        print(
            "Tag colors: Yellow=CORE_MISSING, Red=CUT, Magenta=MAYBE_CUT, Green=ADD, Cyan=MAYBE_ADD, "
            "Blue=SPICY, Gray=WATCH/CORE. Uncolored=KEEP/COMP_ONLY/OTHER."
        )
        return

    parts = [
        wrap("Yellow=CORE_MISSING", ["yellow"], ansi_on),
        wrap("Red=CUT", ["red"], ansi_on),
        wrap("Magenta=MAYBE_CUT", ["magenta"], ansi_on),
        wrap("Green=ADD", ["green"], ansi_on),
        wrap("Cyan=MAYBE_ADD", ["cyan"], ansi_on),
        wrap("Blue=SPICY", ["blue"], ansi_on),
        wrap("Gray=WATCH/CORE", ["gray"], ansi_on),
        "Uncolored=KEEP/COMP_ONLY/OTHER.",
    ]
    print("Tag colors: " + ", ".join(parts))


def print_best_vs_compare_table(
        target_key: str,
        compare: str,
        best_n: int,
        best_counts: Counter,
        comp_n: int,
        comp_counts: Counter,
        best_ranks: List[int],
        deck_cards: Optional[Set[str]],
        ansi_on: bool,
) -> List[Tuple[float, float, float, str]]:
    compare_mode = _normalize_compare_mode(compare)
    deck_cards = deck_cards or set()

    print("\n=== Card inclusion: BEST-placing deck vs comparison ===")
    print(f"Commander(s): {target_key}")

    if best_ranks:
        best_rank_str = f"best ranks observed: min={min(best_ranks)}, median={median_int(best_ranks)}, max={max(best_ranks)}"
    else:
        best_rank_str = "best ranks observed: n/a"

    print(f"Best-of-event sample (one best deck per event): {best_n} ({best_rank_str})")
    print(f"Comparison sample ({compare_mode}): {comp_n}")

    if best_n == 0:
        print("\nNo decks found for that commander set in this time window.")
        return []
    if comp_n == 0:
        print("\nNo comparison decks found for that commander set in the chosen group (try --compare all_non_best).")
        return []

    all_cards = set(best_counts) | set(comp_counts)
    rows: List[Tuple[float, float, float, str]] = []
    for c in all_cards:
        best_pct = best_counts.get(c, 0) / best_n * 100
        comp_pct = comp_counts.get(c, 0) / comp_n * 100
        diff = best_pct - comp_pct
        rows.append((diff, best_pct, comp_pct, c))

    # Sorting: Diff desc, then Best% desc, then Comp% desc, then name (reverse) due to reverse=True
    rows.sort(reverse=True)

    print(f"\n{' ':1} {'Card':57} {'Best%':>7} {'Comp%':>7} {'Diff':>7} {'Tag':>12}")
    print("-" * 97)

    for diff, best_pct, comp_pct, c in rows[:80]:
        in_deck = norm(c) in deck_cards if deck_cards else False
        tag = compute_tag(in_deck=in_deck, best_pct=best_pct, comp_pct=comp_pct, diff=diff)
        prefix = "*" if in_deck else " "
        base = f"{prefix} {c:57} {best_pct:7.1f} {comp_pct:7.1f} {diff:7.1f} {tag:>12}"

        # Bold for in-deck, color by tag
        codes: List[str] = []
        if in_deck:
            codes.append("bold")
        codes += tag_style(tag)

        print(wrap(base, codes, enabled=ansi_on))

    if deck_cards:
        print("\nRow key: '*' prefix = present in submitted Moxfield deck (bold when ANSI enabled).")
    print_tag_legend(ansi_on)
    return rows


# ----------------------------
# Main
# ----------------------------
def main():
    args = parse_args()

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        print(f"Missing API key env var: {args.api_key_env}", file=sys.stderr)
        sys.exit(2)

    ansi_on = ansi_enabled()

    payload = build_payload(args)
    tournaments = fetch_tournaments(args.base_url, api_key, payload)

    if args.save_json:
        with open(args.save_json, "w", encoding="utf-8") as f:
            json.dump(tournaments, f, ensure_ascii=False, indent=2)

    print(f"Fetched {len(tournaments)} tournaments.")

    if not args.no_sanity:
        t0 = tournaments[0] if tournaments else {}
        print("Top-level keys on first tournament:", sorted(list(t0.keys())))

        standings = t0.get("standings")
        if isinstance(standings, list):
            print("standings entries:", len(standings))
            if standings:
                print("standings[0] keys:", sorted(list((standings[0] or {}).keys())))
                d0 = (standings[0] or {}).get("deckObj") or {}
                if d0:
                    print("deckObj keys:", sorted(list(d0.keys())))
        else:
            print("No 'standings' array found on tournament object (may be returned differently by API).")

    if not tournaments:
        print("No tournaments returned. Try increasing --last or lowering --participant-min.")
        return

    counts = discover_commander_sets(tournaments, discover_from=args.discover_from, top_y=args.topY)
    if not counts:
        print("No commander sets discovered. (Unexpected: standings/deckObj may be missing.)")
        return

    options = counts.most_common(args.show)

    if args.commander:
        if "|" in args.commander:
            parts = [norm(p) for p in args.commander.split("|") if norm(p)]
            target_key = commander_key(parts)
        else:
            target_key = norm(args.commander)
    else:
        target_key = prompt_select_commander_set(options, allow_custom=True)

    deck_card_set: Optional[Set[str]] = None
    if args.moxfield:
        try:
            deck_json = fetch_moxfield_deck_json_single_try(args.moxfield, timeout_s=DEFAULTS["moxfield_timeout_s"])
            vis = (deck_json.get("visibility") or "").lower()
            deck_name = deck_json.get("name") or "Unknown"
            if vis and vis != "public":
                print(
                    f"\nMoxfield deck '{deck_name}' visibility is not public.\n"
                    "Please change the deck to Public in Moxfield before comparison will work.\n"
                    "Continuing without deck-aware tags."
                )
            else:
                deck_cards = parse_moxfield_json_to_cards(deck_json)
                deck_card_set = set(deck_cards.keys())
                print(f"\nLoaded Moxfield deck: {deck_name} (unique cards={len(deck_card_set)})")
        except Exception as e:
            print(f"\nMoxfield load failed; continuing without deck-aware tags: {e}", file=sys.stderr)

    best_n, best_counts, comp_n, comp_counts, best_ranks = run_best_vs_compare(
        tournaments, target_key=target_key, compare=args.compare, top_y=args.topY
    )

    _ = print_best_vs_compare_table(
        target_key=target_key,
        compare=args.compare,
        best_n=best_n,
        best_counts=best_counts,
        comp_n=comp_n,
        comp_counts=comp_counts,
        best_ranks=best_ranks,
        deck_cards=deck_card_set,
        ansi_on=ansi_on,
    )


if __name__ == "__main__":
    main()
