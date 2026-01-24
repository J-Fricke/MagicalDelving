# MagicalDelving — TopDeck cEDH Meta Diff (with Moxfield highlight)

A small CLI tool that pulls recent cEDH tournament results from **TopDeck.gg** and compares card inclusion rates between:

- the **best-placing deck per event** for a selected commander set, and
- a configurable **comparison group** (e.g., other Top 16 decks for that commander set, excluding the best deck).

Optionally, you can provide a **Moxfield deck link** to highlight cards that are already in your list.

---

## Features

- Fetch tournaments from TopDeck.gg `/api/v2/tournaments` (POST).
- Discover common commander sets from recent events and select one interactively.
- Compare **Best% vs Comp%** card inclusion and compute **Diff = Best% - Comp%**.
- Colorized console output (auto-detected; no flags required).
- Optional Moxfield integration:
  - Accepts a Moxfield deck URL or deck id
  - Highlights rows that are in your deck
  - If the deck is unlisted/private, prints a message and continues without deck-aware tags

---

## Requirements

- Python 3.10+ recommended
- `requests`

Install dependencies:

```bash
pip install -r requirements.txt
# or
pip install requests
```

---

## API Key Setup (TopDeck.gg)

This script expects your TopDeck API key in an environment variable.

Default env var name: `TOPDECK_API_KEY`

### macOS / Linux (bash/zsh)

```bash
export TOPDECK_API_KEY="YOUR_KEY_HERE"
python topdeck_meta.py --last 30
```

### Windows (PowerShell)

```powershell
$env:TOPDECK_API_KEY="YOUR_KEY_HERE"
python topdeck_meta.py --last 30
```

---

## Usage

Basic run:

```bash
python topdeck_meta.py
```

Show CLI flags:

```bash
python topdeck_meta.py -h
python topdeck_meta.py --help
```

Example (common debug run):

```bash
python topdeck_meta.py --last 30
```

Example (load your Moxfield deck and highlight cards):

```bash
python topdeck_meta.py --last 30 --moxfield "https://moxfield.com/decks/JUaSlpi5W0qmqYpHaqkOEA"
```

Example (skip the interactive commander selection):

```bash
python topdeck_meta.py --last 30 --commander "Tymna the Weaver|Dargo, the Shipwrecker"
```

Example (choose compare pool):

```bash
python topdeck_meta.py --last 30 --compare topY_excluding_best
python topdeck_meta.py --last 30 --compare all_non_best
python topdeck_meta.py --last 30 --compare rest
```

---

## How the comparison works

For each tournament:
1. Find all decks of the chosen commander set (based on `deckObj.Commanders`).
2. Select the **best-placing** deck of that commander set in that tournament (first occurrence in standings order).
3. Add that deck to the **Best-of-event** sample.
4. Add the remaining decks of that commander set to the **Comparison** pool according to `--compare`:
   - `topY_excluding_best`: decks within the Top `--topY` cut excluding the best deck
   - `rest`: decks outside Top `--topY` excluding the best deck
   - `all_non_best`: all other decks of that commander set excluding the best deck

Then the tool computes inclusion rates for each card:

- `Best%` = (best decks containing card) / (number of best decks) * 100  
- `Comp%` = (comparison decks containing card) / (number of comparison decks) * 100  
- `Diff` = `Best% - Comp%`

Rows are currently sorted by **Diff descending**.

---

## Tagging & colors

Every row has a tag.

High-signal tags (colored):

- **Yellow**: `CORE_MISSING` — staple in best decks, missing from your deck
- **Red**: `CUT`
- **Magenta**: `MAYBE_CUT`
- **Green**: `ADD`
- **Cyan**: `MAYBE_ADD`
- **Blue**: `SPICY` — winner-skewed tech that’s not common enough for MAYBE_ADD
- **Gray**: `WATCH` — appears in best decks but not enough to recommend

Low-signal tags (uncolored but still present):

- `CORE` — staple and already present in your deck
- `KEEP` — in your deck, no other tag triggered
- `COMP_ONLY` — appears only in comparison pool, not best pool
- `OTHER` — fallback (rare)

If you provide a Moxfield deck, rows for cards in your list are:
- prefixed with `*`
- bolded when ANSI color is enabled

---

## Moxfield notes

- Provide either a deck URL or just the deck id:
  - `https://moxfield.com/decks/<id>`
  - `<id>`
- The script fetches deck JSON via:
  - `https://api2.moxfield.com/v2/decks/all/<id>`
- If the deck is **unlisted/private**, Moxfield often returns 401/403/404. The script will:
  - tell you to set the deck to **Public**
  - continue without deck-aware highlighting

---

## Common troubleshooting

### `ModuleNotFoundError: No module named 'requests'`

Install dependencies:

```bash
pip install requests
```

### TopDeck 502 / Cloudflare errors

The script retries a few times with backoff. If it still fails:
- try again later
- reduce the scope (e.g., smaller `--last`)
- raise `participant-min` to reduce returned events

### No tournaments fetched
Try:
- increasing `--last`
- lowering `--participant-min`

---

## Repo layout suggestion

```
magicaldelving/
  topdeck_meta.py
  README.md
  requirements.txt
  .gitignore
```

`requirements.txt`:

```
requests
```

---

## License

MIT. See `LICENSE`.
