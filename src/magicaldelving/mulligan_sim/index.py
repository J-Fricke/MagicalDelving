from __future__ import annotations

from typing import Dict, Optional, Set, Tuple

from .card_facts import (
    CardFacts,
    face_has_burst_from_creatures,
    face_has_creature_tap_mana_enabler,
    face_has_tap_add,
)
from .models import Permanent


class CardIndex:
    """Light wrapper around card facts + roles (with face-aware helpers)."""

    def __init__(self, facts_roles: Dict[str, Tuple[CardFacts, Set[str]]]):
        self._m = facts_roles

    # ---- name-based ----

    def facts(self, name: str) -> Optional[CardFacts]:
        v = self._m.get(name)
        return v[0] if v else None

    def roles(self, name: str) -> Set[str]:
        v = self._m.get(name)
        return set(v[1]) if v else set()

    def mv(self, name: str) -> float:
        f = self.facts(name)
        return float(f.mana_value) if f else 0.0

    def is_land(self, name: str) -> bool:
        f = self.facts(name)
        return bool(f and f.is_land)

    # ---- permanent-based (face-aware) ----

    def facts_for_perm(self, perm: Permanent) -> Optional[CardFacts]:
        return self.facts(perm.name)

    def oracle_for_perm(self, perm: Permanent) -> str:
        f = self.facts_for_perm(perm)
        return f.face_oracle_text(perm.face) if f else ""

    def type_line_for_perm(self, perm: Permanent) -> str:
        # Cards: printed type line. Tokens: synthesize from current types/subtypes.
        if perm.is_card:
            f = self.facts_for_perm(perm)
            return f.face_type_line(perm.face) if f else ""
        tl = " ".join(sorted(perm.types))
        if perm.subtypes:
            tl += " — " + " ".join(sorted(perm.subtypes))
        return tl

    def _has_type(self, perm: Permanent, type_name: str) -> bool:
        if type_name in perm.type_overrides_remove:
            return False
        if type_name in perm.type_overrides_add:
            return True
        if not perm.is_card:
            return type_name in perm.types
        return type_name in self.type_line_for_perm(perm)

    def is_creature_perm(self, perm: Permanent) -> bool:
        return self._has_type(perm, "Creature")

    def is_land_perm(self, perm: Permanent) -> bool:
        return self._has_type(perm, "Land")

    def is_artifact_perm(self, perm: Permanent) -> bool:
        return self._has_type(perm, "Artifact")

    def roles_for_perm(self, perm: Permanent) -> Set[str]:
        """Roles adjusted for the current face (for transform / modal cards)."""
        base = set(self.roles(perm.name))
        f = self.facts_for_perm(perm)
        if not f:
            return base

        has_tap = face_has_tap_add(f, perm.face)
        has_enabler = face_has_creature_tap_mana_enabler(f, perm.face)
        has_burst = face_has_burst_from_creatures(f, perm.face)

        for r in ("CreatureTapManaEnabler", "BurstManaFromCreatures", "ManaDork", "ManaRock"):
            base.discard(r)

        if has_enabler:
            base.add("CreatureTapManaEnabler")
        if has_burst:
            base.add("BurstManaFromCreatures")
        if has_tap:
            if self.is_creature_perm(perm):
                base.add("ManaDork")
            if self.is_artifact_perm(perm):
                base.add("ManaRock")

        return base
