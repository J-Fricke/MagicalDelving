import argparse
import json
import sys
from typing import Optional


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="mulligan-sim",
        description="Commander mulligan + draw/win-turn Monte Carlo simulator",
    )

    src = ap.add_mutually_exclusive_group(required=False)
    src.add_argument(
        "--deck",
        default=None,
        help="Decklist path, or '-' to read from stdin",
    )
    src.add_argument(
        "--moxfield",
        default=None,
        help="Moxfield deck URL or deck id (e.g. https://moxfield.com/decks/<id> or <id>)",
    )

    ap.add_argument("--iters", type=int, default=50_000, help="Number of simulations")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed (optional)")
    ap.add_argument("--max-mulls", type=int, default=2, help="Max mulligans (London)")

    ap.add_argument("--draw-by", type=int, default=5, help="Turn by which we want draw online")
    ap.add_argument("--win-by", type=int, default=8, help="Turn by which we want to win")
    ap.add_argument("--damage-threshold", type=int, default=120, help="Damage threshold to count as win")

    ap.add_argument(
        "--offline",
        action="store_true",
        help="Do not make network calls (Moxfield/Scryfall). Requires warm cache / local decklist.",
    )
    ap.add_argument(
        "--scryfall-cache",
        default=None,
        help="Override Scryfall cache path (default: ~/.cache/magicaldelving/scryfall_cache.json)",
    )

    ap.add_argument("--json", action="store_true", help="Output JSON instead of text")

    return ap.parse_args(argv)


def read_deck_text(path: str) -> str:
    if path == "-" or (path or "").strip() == "":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main() -> int:
    args = parse_args()

    from magicaldelving.mulligan_sim.deck_parser import parse_deck_text
    from magicaldelving.mulligan_sim.sim_core import CardIndex, SimConfig, SimGoals, run_sim
    from magicaldelving.mulligan_sim.card_facts import build_facts_and_roles
    from magicaldelving.scryfall import ScryfallClient

    deck_text: str

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
        # default: read from --deck if provided, otherwise stdin
        path = args.deck if args.deck is not None else "-"
        deck_text = read_deck_text(path)

    try:
        deck = parse_deck_text(deck_text)
    except Exception as e:
        print(f"ERROR: Failed to parse decklist: {e}", file=sys.stderr)
        return 2

    # Build card facts map via Scryfall (+ cache)
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

    goals = SimGoals(draw_by_turn=args.draw_by, win_by_turn=args.win_by, damage_threshold=args.damage_threshold)
    cfg = SimConfig(trials=args.iters, seed=args.seed)

    results = run_sim(deck=deck, card_index=idx, goals=goals, cfg=cfg, max_mulls=args.max_mulls)

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
        return 0

    print(f"trials={results.get('trials')}  draw_ok={results.get('draw_ok_rate'):.3f}  win_ok={results.get('win_ok_rate'):.3f}")
    dist = results.get("first_win_turn_dist") or {}
    if dist:
        # show earliest few turns
        earliest = sorted((int(k), int(v)) for k, v in dist.items())[:8]
        print("first_win_turns:")
        for t, c in earliest:
            print(f"  T{t}: {c}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
