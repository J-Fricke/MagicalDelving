# Magical Delving — A Suite of cEDH Tools

Magical Delving is a growing suite of tools for analyzing and improving EDH decks with a lean towards competitive and metagames.

**Tool:**
- **TopDeck Meta Diff** — compares card inclusion rates for a chosen commander set between:
  - the **best-placing deck per event** (for that commander set), and
  - a configurable **comparison pool** (e.g., other top-cut decks for that commander set, excluding the best deck).
- **Mulligan Simulator** - simulates mulligans to establish if a deck can get a draw engine/mana/win by certain turns
  - load moxfield deck to start simulating, set your conditions and if needed overload logic some.

Optional: provide a **Moxfield deck link** to highlight cards already in your list.

---

## Requirements

- Python 3.10+ recommended
- TopDeck.gg API key (set via env var)
- `requests` (installed automatically if you use the install steps below)

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

## Run the tool

You now have two equivalent ways to run it:

### Option A: direct tool command
```bash
topdeck-meta --last 30
```

### Option B: suite command (subcommand)
```bash
magicaldelving topdeck-meta --last 30
```

Show flags/help:
```bash
topdeck-meta -h
magicaldelving -h
magicaldelving topdeck-meta -h
```

---

## Moxfield highlighting

Provide either a deck URL or deck id:

```bash
topdeck-meta --last 30 --moxfield "https://moxfield.com/decks/JUaSlpi5W0qmqYpHaqkOEA"
# or
topdeck-meta --last 30 --moxfield "JUaSlpi5W0qmqYpHaqkOEA"
```

If the deck is **not public** (unlisted/private), Moxfield may return 401/403/404. The tool will warn you and continue without deck-aware tags.

---

## How the comparison works (TopDeck Meta Diff)

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
      topdeck_meta/
        __init__.py
        tool.py

  tests/
    (optional)
```

---

## License

MIT. See `LICENSE`.
