from __future__ import annotations
from typing import Set

# NOTE: keep sim defaults in one place (tool.py should import these)
DEFAULT_TRIALS = 50_000
DEFAULT_DRAW_BY = 5
DEFAULT_WIN_BY = 8
DEFAULT_DAMAGE_THRESHOLD = 120
DEFAULT_MAX_MULLS = 3
DEFAULT_MAX_TURNS = 25

ACTION_ROLES: Set[str] = {
    "DrawEngine",
    "Refill",
    "TokenMaker",
    "TokenBurst",
    "Finisher",
    "Wincon",
}
