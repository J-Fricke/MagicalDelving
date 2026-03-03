import argparse
import json
import os
import sys
from typing import Optional, Set, List, TextIO
from .defaults import (
    DEFAULT_TRIALS,
    DEFAULT_DRAW_BY,
    DEFAULT_WIN_BY,
    DEFAULT_DAMAGE_THRESHOLD,
    DEFAULT_MAX_MULLS,
    DEFAULT_MAX_TURNS,
)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="mulligan-sim",
        description="Commander mulligan + draw/win-turn Monte Carlo simulator",
    )

    src = ap.add_mutually_exclusive_group(required=False)
    src.add_argument("--deck", default=None, help="Decklist path, or '-' to read from stdin")
    src.add_argument("--moxfield", default=None, help="Moxfield deck URL or deck id")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed (optional)")
    ap.add_argument("--offline", action="store_true", help="No network calls (requires warm cache / local decklist)")
    ap.add_argument("--scryfall-cache", default=None, help="Override Scryfall cache path")
    ap.add_argument("--json", action="store_true", help="Output JSON instead of text")
    ap.add_argument("--explain-roles", action="store_true", help="Print role classification (moxfield/tag/oracle)")
    ap.add_argument("--verbose", action="store_true", help="Print extra details (e.g., win turn distribution)")
    ap.add_argument("--iters", type=int, default=DEFAULT_TRIALS, help="Number of simulations")
    ap.add_argument("--max-mulls", type=int, default=DEFAULT_MAX_MULLS, help="Max mulligans (London)")
    ap.add_argument("--draw-by", type=int, default=DEFAULT_DRAW_BY, help="Turn by which we want draw online")
    ap.add_argument("--win-by", type=int, default=DEFAULT_WIN_BY, help="Turn by which we want to win")
    ap.add_argument("--damage-threshold", type=int, default=DEFAULT_DAMAGE_THRESHOLD, help="Damage threshold to count as win")
    ap.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="Simulate up to this turn (safety cap)")

    return ap.parse_args(argv)


def read_deck_text(path: str) -> str:
    if path == "-" or (path or "").strip() == "":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _role_sources(
        inline_tags: Set[str],
        oracle_roles: Set[str],
        role: str,
        roles_from_tags_fn,
) -> List[str]:
    mx_tags = {t for t in inline_tags if t.startswith("Mx:")}
    manual_tags = set(inline_tags) - mx_tags

    mx_roles = roles_from_tags_fn(mx_tags)
    manual_roles = roles_from_tags_fn(manual_tags)

    src: List[str] = []
    if role in mx_roles:
        src.append("moxfield")
    elif role in manual_roles:
        src.append("tag")

    if role in oracle_roles:
        src.append("oracle")

    return src


def _print_role_report(deck, idx, out: TextIO) -> None:
    from .card_facts import infer_roles, roles_from_tags

    ROLE_SET = ["DrawEngine", "Refill", "Ramp", "Wincon", "Finisher"]
    buckets = {r: [] for r in ROLE_SET}

    for name in sorted(deck.counts.keys(), key=str.lower):
        facts = idx.facts(name)
        if facts is None:
            continue

        inline_tags = set(deck.inline_tags.get(name, set()))
        oracle_roles = infer_roles(facts)
        all_roles = idx.roles(name)

        for role in ROLE_SET:
            if role not in all_roles:
                continue
            src = _role_sources(inline_tags, oracle_roles, role, roles_from_tags)
            label = f"{name} ({'+'.join(src)})" if src else name
            buckets[role].append(label)

    print("\nrole_classification:", file=out)
    pretty_map = {
        "DrawEngine": "Draw engines",
        "Refill": "Refills (one-shot draw)",
        "Ramp": "Ramp",
        "Wincon": "Wincons",
        "Finisher": "Finishers",
    }
    for role in ROLE_SET:
        xs = buckets.get(role) or []
        print(f"{pretty_map[role]} ({len(xs)}):", file=out)
        for x in xs:
            print(f"  - {x}", file=out)
        print("", file=out)


def _should_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def _bold(s: str, color: bool) -> str:
    return f"\x1b[1m{s}\x1b[0m" if color else s

def _dim(s: str, color: bool) -> str:
    # ANSI "faint" + grey fallback. Most terminals render this as dim grey.
    return f"\x1b[2m\x1b[90m{s}\x1b[0m" if color else s

def _print_dim(line: str, color: bool) -> None:
    print(_dim(line, color))


def _pct(x: float, color: bool) -> str:
    val = f"{(x * 100.0):.1f}%"
    if not color:
        return val
    # green
    return f"\x1b[32m{val}\x1b[0m"


def main() -> int:
    args = parse_args()

    from .deck_parser import parse_deck_text
    from .index import CardIndex
    from .models import SimConfig, SimGoals
    from .runner import run_sim
    from .card_facts import build_facts_and_roles
    from magicaldelving.scryfall import ScryfallClient

    # Load deck text
    if args.moxfield:
        if args.offline:
            print("ERROR: --offline cannot be used with --moxfield (needs network).", file=sys.stderr)
            return 2

        from magicaldelving.moxfield import fetch_deck_json_single_try, deck_json_to_deck_text

        try:
            deck_json = fetch_deck_json_single_try(args.moxfield)
        except Exception as e:
            print(f"ERROR: Failed to fetch Moxfield deck: {e}", file=sys.stderr)
            return 2
        deck_text = deck_json_to_deck_text(deck_json)
    else:
        deck_text = read_deck_text(args.deck if args.deck is not None else "-")

    # Parse
    try:
        deck = parse_deck_text(deck_text)
    except Exception as e:
        print(f"ERROR: Failed to parse decklist: {e}", file=sys.stderr)
        return 2

    # Scryfall facts (+ cache)
    uniq_names = sorted(deck.counts.keys(), key=str.lower)
    scry = ScryfallClient(cache_path=args.scryfall_cache, offline=bool(args.offline))
    try:
        found_map, missing = scry.fetch_many_by_name(uniq_names)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if missing:
        print("ERROR: Missing cards from Scryfall/cache:", file=sys.stderr)
        for n in missing:
            print(f"  - {n}", file=sys.stderr)
        print("\nTip: run once without --offline to warm the cache.", file=sys.stderr)
        return 2

    facts_roles = build_facts_and_roles(found_map, inline_tags=deck.inline_tags)
    idx = CardIndex(facts_roles)

    # explain output should not break JSON
    if args.explain_roles:
        _print_role_report(deck, idx, sys.stderr if args.json else sys.stdout)

    goals = SimGoals(draw_by_turn=args.draw_by, win_by_turn=args.win_by, damage_threshold=args.damage_threshold)
    cfg = SimConfig(trials=args.iters, seed=args.seed, max_turns=args.max_turns)

    results = run_sim(deck=deck, card_index=idx, goals=goals, cfg=cfg, max_mulls=args.max_mulls)

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0

    out = sys.stdout
    color = _should_color(out)

    trials = int(results.get("trials") or args.iters)
    draw_rate = float(results.get("draw_ok_rate") or 0.0)
    win_rate = float(results.get("win_ok_rate") or 0.0)
    draw_count = int(results.get("draw_ok_count") or round(draw_rate * trials))
    win_count = int(results.get("win_ok_count") or round(win_rate * trials))
    dist = results.get("first_win_turn_dist") or {}
    max_win_turn = results.get("max_win_turn")
    delta = results.get("avg_to_max_delta_capped")
    avg_win_turn = None
    if dist:
        total = sum(int(v) for v in dist.values())
        if total > 0:
            avg_win_turn = sum(int(t) * int(v) for t, v in dist.items()) / total

    # RESULTS FIRST
    avg_str = f"{avg_win_turn:.1f}" if avg_win_turn is not None else "N/A"
    print(
        f"{_bold('Results', color)} - "
        f"Draw By({args.draw_by}): {_pct(draw_rate, color)} "
        f"Win By({args.win_by}): {_pct(win_rate, color)} "
        f"Avg Win Turn: {avg_str}"
        f"Max Win Turn: {args.max_win_turn}"
        f"Delta: {args.avg_to_max_delta_capped}"
    )
    print("")  # blank line

    # summary AFTER (counts live here)
    _print_dim(
        f"summary: trials={trials} max_mulls={args.max_mulls} dmg>={args.damage_threshold} "
        f"draw_ok={draw_count}/{trials} win_ok={win_count}/{trials}",
        color,
    )

    if args.verbose and dist:
        earliest = sorted((int(k), int(v)) for k, v in dist.items())[:12]
        _print_dim("first_win_turns:", color)
        for t, c in earliest:
            _print_dim(f"  T{t}: {c}", color)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())