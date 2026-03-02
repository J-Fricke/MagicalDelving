from __future__ import annotations
from typing import Dict, Optional, Set, Tuple
from .card_facts import CardFacts


class CardIndex:
    """Light wrapper around card facts + roles."""

    def __init__(self, facts_roles: Dict[str, Tuple[CardFacts, Set[str]]]):
        self._m = facts_roles

    def facts(self, name: str) -> Optional[CardFacts]:
        v = self._m.get(name)
        return v[0] if v else None

    def roles(self, name: str) -> Set[str]:
        v = self._m.get(name)
        return set(v[1]) if v else set()

    def is_land(self, name: str) -> bool:
        f = self.facts(name)
        return bool(f and f.is_land)

    def mv(self, name: str) -> float:
        f = self.facts(name)
        return float(f.mana_value) if f else 0.0