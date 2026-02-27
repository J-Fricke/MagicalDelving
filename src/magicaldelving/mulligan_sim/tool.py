import argparse
import json
import sys
from typing import Optional


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="mulligan-sim",
        description="Commander mulligan + draw/win-turn Monte Carlo simulator",
    )

    ap.add_argument(
        "--deck",
        default="-",
        help="Decklist path, or '-' to read from stdin (default: '-')",
    )
    ap.add_argument("--iters", type=int, default=50_000, help="Number of simulations")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed (optional)")
    ap.add_argument("--max-mulls", type=int, default=2, help="Max mulligans (London)")
    ap.add_argument(
        "--policy",
        default="default",
        help="Mulligan policy name (wired to your deck_logic / policy module)",
    )

    ap.add_argument("--json", action="store_true", help="Output JSON instead of text")

    return ap.parse_args(argv)


def read_deck_text(path: str) -> str:
    if path == "-" or path.strip() == "":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def main() -> int:
    args = parse_args()

    # Import here so "magicaldelving -h" works even if sim deps change later
    from magicaldelving.mulligan_sim.deck_parser import parse_decklist
    from magicaldelving.mulligan_sim.card_db import load_card_db
    from magicaldelving.mulligan_sim.sim_core import run_sim

    deck_text = read_deck_text(args.deck)
    deck = parse_decklist(deck_text)
    card_db = load_card_db()

    results = run_sim(
        deck=deck,
        card_db=card_db,
        iters=args.iters,
        seed=args.seed,
        max_mulls=args.max_mulls,
        policy=args.policy,
    )

    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        # Keep this human summary tiny; you can expand later
        print(f"iters={results.get('iters')}  win_by_T={results.get('win_by_turn')}  draw_by_T={results.get('draw_by_turn')}")
        if "notes" in results:
            for n in results["notes"]:
                print(f"- {n}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())