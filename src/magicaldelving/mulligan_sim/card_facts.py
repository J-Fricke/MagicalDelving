from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Set, Tuple


def _coerce_int(s: Any) -> Optional[int]:
    try:
        if s is None:
            return None
        if isinstance(s, int):
            return s
        if isinstance(s, str) and s.strip().lstrip("+-").isdigit():
            return int(s)
    except Exception:
        return None
    return None


def _faces(card_json: Dict[str, Any]) -> Tuple[Dict[str, Any], ...]:
    faces = card_json.get("card_faces")
    if isinstance(faces, list) and faces:
        out = []
        for f in faces:
            if isinstance(f, dict):
                out.append(f)
        if out:
            return tuple(out)
    return (card_json,)


def _join_face_field(card_json: Dict[str, Any], field: str) -> str:
    vals = []
    for f in _faces(card_json):
        v = f.get(field)
        if isinstance(v, str) and v.strip():
            vals.append(v.strip())
    top = card_json.get(field)
    if isinstance(top, str) and top.strip():
        if top.strip() not in vals:
            vals.insert(0, top.strip())
    return "\n//\n".join(vals)


def _any_face_type_contains(card_json: Dict[str, Any], needle: str) -> bool:
    for f in _faces(card_json):
        tl = f.get("type_line")
        if isinstance(tl, str) and needle.lower() in tl.lower():
            return True
    tl = card_json.get("type_line")
    return isinstance(tl, str) and needle.lower() in tl.lower()


# ----------------------------
# Tag normalization
# ----------------------------

def _roles_from_tag(tag: str) -> Set[str]:
    """
    Normalize decklist / Moxfield tags into the small role vocabulary used by mulligan_sim.

    We accept both:
      - direct role tags: Ramp / DrawEngine / Refill / Wincon / Damage / Evasion / ExtraCombat
      - loose/synonym tags: "Draw", "Card Advantage", "Wheel", "Combo", etc.
      - namespaced Moxfield tags: "Mx:<Category>"
    """
    raw = (tag or "").strip()
    if not raw:
        return set()

    t = raw
    if t.lower().startswith("mx:"):
        t = t[3:].strip()

    low = t.lower()
    out: Set[str] = set()

    direct = {
        "ramp": "Ramp",
        "drawengine": "DrawEngine",
        "refill": "Refill",
        "wincon": "Wincon",
        "damage": "Damage",
        "evasion": "Evasion",
        "extracombat": "ExtraCombat",
    }
    key = low.replace(" ", "")
    if key in direct:
        out.add(direct[key])
        return out

    if any(k in low for k in ["ramp", "mana", "rock", "dork", "accelerat"]):
        out.add("Ramp")

    if any(k in low for k in ["draw", "card advantage", "advantage", "engine"]):
        out.add("DrawEngine")
    if any(k in low for k in ["wheel", "refill", "reload"]):
        out.add("Refill")

    if any(k in low for k in ["win", "combo", "finisher", "payoff", "wincon"]):
        out.add("Wincon")

    if any(k in low for k in ["evasion", "unblock", "flying", "menace", "trample"]):
        out.add("Evasion")
    if any(k in low for k in ["extra combat", "combat step"]):
        out.add("ExtraCombat")
    if any(k in low for k in ["threat", "damage", "beatdown", "pressure"]):
        out.add("Damage")

    return out


def _augment_roles_with_tags(roles: Set[str], tags: Set[str]) -> None:
    """
    Add both the raw tags (for future/diagnostic use) and the normalized roles derived from them.
    """
    for t in tags:
        if not t:
            continue
        roles.add(t)
        roles |= _roles_from_tag(t)


@dataclass(frozen=True)
class CardFacts:
    name: str
    mana_value: float
    type_line: str
    oracle_text: str
    is_land: bool
    is_creature: bool
    is_artifact: bool
    is_enchantment: bool
    is_instant: bool
    is_sorcery: bool
    power: Optional[int]

    @staticmethod
    def from_scryfall(card_json: Dict[str, Any]) -> "CardFacts":
        name = str(card_json.get("name") or "").strip() or "UNKNOWN"
        mv_raw = card_json.get("cmc")
        try:
            mv = float(mv_raw) if mv_raw is not None else 0.0
        except Exception:
            mv = 0.0

        type_line = _join_face_field(card_json, "type_line")
        oracle_text = _join_face_field(card_json, "oracle_text")

        is_land = _any_face_type_contains(card_json, "Land")
        is_creature = _any_face_type_contains(card_json, "Creature")
        is_artifact = _any_face_type_contains(card_json, "Artifact")
        is_enchantment = _any_face_type_contains(card_json, "Enchantment")
        is_instant = _any_face_type_contains(card_json, "Instant")
        is_sorcery = _any_face_type_contains(card_json, "Sorcery")

        p = None
        for f in _faces(card_json):
            p = _coerce_int(f.get("power"))
            if p is not None:
                break

        return CardFacts(
            name=name,
            mana_value=mv,
            type_line=type_line,
            oracle_text=oracle_text,
            is_land=is_land,
            is_creature=is_creature,
            is_artifact=is_artifact,
            is_enchantment=is_enchantment,
            is_instant=is_instant,
            is_sorcery=is_sorcery,
            power=p,
        )


def infer_roles(facts: CardFacts) -> Set[str]:
    """Heuristic role inference from Scryfall facts."""
    txt = (facts.oracle_text or "").lower()
    roles: Set[str] = set()

    if not facts.is_land:
        if "add {" in txt:
            if facts.is_artifact or facts.is_creature or facts.is_enchantment:
                roles.add("Ramp")
        if "search your library" in txt and "land" in txt and ("onto the battlefield" in txt or "put" in txt):
            roles.add("Ramp")

    if "draw" in txt and "card" in txt:
        if facts.is_instant or facts.is_sorcery:
            roles.add("Refill")
        else:
            if "whenever" in txt or "at the beginning" in txt or "each" in txt:
                roles.add("DrawEngine")

    if "you win the game" in txt or "wins the game" in txt:
        roles.add("Wincon")

    if facts.is_creature:
        if facts.power is not None and facts.power >= 4:
            roles.add("Damage")
        if any(k in txt for k in ["flying", "menace", "trample", "unblockable", "can't be blocked", "fear", "intimidate", "shadow"]):
            roles.add("Evasion")

    if "additional combat phase" in txt or "additional combat" in txt:
        roles.add("ExtraCombat")

    return roles


def build_facts_and_roles(
        scryfall_cards_by_input_name: Dict[str, Dict[str, Any]],
        inline_tags: Dict[str, Set[str]] | None = None,
) -> Dict[str, Tuple[CardFacts, Set[str]]]:
    """Return mapping card_name -> (facts, roles)."""
    inline_tags = inline_tags or {}
    out: Dict[str, Tuple[CardFacts, Set[str]]] = {}

    for input_name, card_json in scryfall_cards_by_input_name.items():
        if not isinstance(card_json, dict):
            continue
        facts = CardFacts.from_scryfall(card_json)
        roles = infer_roles(facts)

        _augment_roles_with_tags(roles, set(inline_tags.get(input_name, set())))
        _augment_roles_with_tags(roles, set(inline_tags.get(facts.name, set())))

        out[facts.name] = (facts, roles)
        out.setdefault(input_name, (facts, roles))

    return out