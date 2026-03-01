# Magical Delving — A Suite of cEDH Tools

Magical Delving is a growing suite of tools for analyzing and improving EDH decks with a lean towards competitive and metagames.

## Tools

- **TopDeck Meta Diff** — compares card inclusion rates for a chosen commander set between:
  - the **best-placing deck per event** (for that commander set), and
  - a configurable **comparison pool** (e.g., other top-cut decks for that commander set, excluding the best deck).
  - Optional: provide a **Moxfield deck link** to highlight cards already in your list.

- **Mulligan Simulator** — simulates mulligans to estimate how often a deck can:
  - establish **mana / acceleration**
  - have **draw online** by a target turn
  - reach a **win condition** by a target turn
  - Load from a **Moxfield deck URL/id** or a local decklist file.

---

## Requirements

- Python 3.10+ recommended
- TopDeck.gg API key (for `topdeck-meta`)
- `requests` (installed automatically via the install steps)

Network access:
- `topdeck-meta` calls TopDeck.gg.
- `mulligan-sim` can call Moxfield + Scryfall. Scryfall lookups are cached on disk so repeat runs are fast, and you can run offline once the cache is warm.

---

## Install (recommended)

From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

This installs Magical Delving in **editable** mode (changes you make are immediately reflected).

---

## TopDeck API Key

Default env var name: `TOPDECK_API_KEY`

### macOS / Linux (bash/zsh)

```bash
export TOPDECK_API_KEY="YOUR_KEY_HERE"
```

### Windows (PowerShell)

```powershell
$env:TOPDECK_API_KEY="YOUR_KEY_HERE"
```

---

## Run tools

You have two equivalent ways to run tools:

### Option A: direct tool command
```bash
topdeck-meta -h
mulligan-sim -h
```

### Option B: suite command (subcommand)
```bash
magicaldelving -h
magicaldelving topdeck-meta -h
magicaldelving mulligan-sim -h
```

---

## TopDeck Meta Diff

Basic run:
```bash
topdeck-meta --last 30
# or
magicaldelving topdeck-meta --last 30
```

### Moxfield highlighting (TopDeck Meta Diff)

Provide either a deck URL or deck id:

```bash
topdeck-meta --last 30 --moxfield "https://moxfield.com/decks/JUaSlpi5W0qmqYpHaqkOEA"
# or
topdeck-meta --last 30 --moxfield "JUaSlpi5W0qmqYpHaqkOEA"
```

If the deck is **not public** (unlisted/private), Moxfield may return 401/403/404. The tool will warn you and continue without deck-aware tags.

### How the comparison works (TopDeck Meta Diff)

For each tournament:
1. Find all decks of the chosen commander set (via `deckObj.Commanders`).
2. Select the **best-placing** deck of that commander set in that tournament (first occurrence in standings order).
3. Add that deck to the **Best-of-event** sample.
4. Add the remaining decks of that commander set to the **Comparison** pool based on `--compare`:
  - `topY_excluding_best`: decks within the top-cut `--topY` excluding the best deck
  - `rest`: decks outside top-cut excluding the best deck
  - `all_non_best`: all other decks of that commander set excluding the best deck

Inclusion rates:
- `Best%` = (best decks containing card) / (best decks) * 100
- `Comp%` = (comparison decks containing card) / (comparison decks) * 100
- `Diff` = `Best% - Comp%`

Rows are sorted by **Diff descending**.

---

## Mulligan Simulator

### Run from a Moxfield deck (recommended)

```bash
mulligan-sim --moxfield "https://moxfield.com/decks/JUaSlpi5W0qmqYpHaqkOEA"
# or
mulligan-sim --moxfield "JUaSlpi5W0qmqYpHaqkOEA"
```

Common knobs:
```bash
mulligan-sim --moxfield "<id-or-url>" --iters 50000 --draw-by 5 --win-by 8
```

### Run from a local decklist

```bash
mulligan-sim --deck ./deck.txt
# or via stdin
cat ./deck.txt | mulligan-sim --deck -
```

### JSON output

```bash
mulligan-sim --moxfield "<id-or-url>" --json
```

### Scryfall caching / offline mode

`mulligan-sim` uses Scryfall to fetch basic card facts (mana value, type line, oracle text) and caches results on disk.

- Default cache path: `~/.cache/magicaldelving/scryfall_cache.json` (respects `XDG_CACHE_HOME` on Linux)
- Override per-run with:

```bash
mulligan-sim --moxfield "<id-or-url>" --scryfall-cache /path/to/cache.json
```

Offline runs:
- Use `--offline` to prevent any network calls (requires a warm Scryfall cache, and a local decklist if you are not using Moxfield):
```bash
mulligan-sim --deck ./deck.txt --offline
```

You can also force cache-only behavior via:
```bash
export MAGICALDELVING_OFFLINE=1
```

---

## Build a wheel (.whl)

From the repo root:

```bash
pip install -U build
python -m build
```

Wheels land in `dist/` as `magicaldelving-<version>-py3-none-any.whl`.

---

## Repo structure (target)

```
MagicalDelving/
  pyproject.toml
  README.md
  LICENSE
  .gitignore

  src/
    magicaldelving/
      __init__.py
      cli.py
      moxfield.py
      scryfall.py

      topdeck_meta/
        __init__.py
        tool.py

      mulligan_sim/
        __init__.py
        tool.py
        deck_parser.py
        sim_core.py
        card_facts.py

  tests/
    (optional)
```

---

## License

MIT. See `LICENSE`.
