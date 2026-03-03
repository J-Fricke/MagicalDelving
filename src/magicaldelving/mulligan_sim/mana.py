from __future__ import annotations

import re
from typing import List, Tuple

from .index import CardIndex
from .models import GameState, Permanent
from .tokens import estimate_tokens_created_from_text


def _oracle_lc(idx: CardIndex, name: str) -> str:
    f = idx.facts(name)
    return (f.oracle_text or "").lower() if f else ""


def _is_x10_haste_pump_finisher(idx: CardIndex, name: str) -> bool:
    """
    Detect Finale-of-Devastation-like text:
      "If X is 10 or more, creatures you control get +X/+X and gain haste until end of turn."
    """
    txt = _oracle_lc(idx, name)
    return ("if x is 10 or more" in txt) and ("creatures you control get +x/+x" in txt) and ("gain haste" in txt)


def _is_greatest_power_trample_pump(idx: CardIndex, name: str) -> bool:
    """
    Detect Overwhelming-Stampede-like text:
      "Until end of turn, creatures you control gain trample and get +X/+X where X is the greatest power among creatures you control."
    """
    txt = _oracle_lc(idx, name)
    return ("gain trample" in txt) and ("get +x/+x" in txt) and ("greatest power" in txt)


def _is_finisher_like(idx: CardIndex, name: str) -> bool:
    txt = _oracle_lc(idx, name)
    if "Finisher" in idx.roles(name):
        return True
    return _is_x10_haste_pump_finisher(idx, name) or _is_greatest_power_trample_pump(idx, name) or (
            ("creatures you control get +" in txt) and ("until end of turn" in txt)
    )


# ---------------------------
# Qty-aware split/tap helpers
# ---------------------------

def _take_units(st: GameState, pid: int, n: int) -> int:
    """
    Ensure there is a battlefield entry representing exactly n units from pid.
    Returns the pid of that extracted group (may be the original pid).
    """
    p = st.battlefield[pid]
    n = int(n)
    if n <= 0:
        raise ValueError("n must be > 0")
    if n > p.qty:
        raise ValueError("not enough quantity")
    if n == p.qty:
        return pid
    return st.split_permanent(pid, n)


def _tap_units(st: GameState, pid: int, n: int = 1) -> int:
    """
    Tap n units from pid (splitting if needed). Returns the pid of the tapped group.
    """
    tapped_pid = _take_units(st, pid, n)
    st.battlefield[tapped_pid].tapped = True
    return tapped_pid


# ---------------------------
# Token creation (legacy helper)
# ---------------------------
# TODO: Replace estimate_tokens_created_from_text + heuristic token parsing with deterministic token resolver (X + where-X).
# TODO: Opponent-derived X counts should use defaults.py scenario knobs, not card names.

_CREATURE_TOKEN_RE = re.compile(
    r"create\s+(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:tapped\s+and\s+attacking\s+)?"
    r"(\d+)\/(\d+)\s+([^\.]+?)\s+token",
    re.IGNORECASE,
)

_WITH_KW = {
    "flying": "Flying",
    "trample": "Trample",
    "haste": "Haste",
    "double strike": "DoubleStrike",
    "vigilance": "Vigilance",
    "menace": "Menace",
    "first strike": "FirstStrike",
    "deathtouch": "Deathtouch",
    "lifelink": "Lifelink",
}


def _add_generic_creature_tokens(st: GameState, source_oracle: str, count: int) -> None:
    """
    Best-effort creature token creation.
    If parsing fails, creates 0/0 Creature tokens (fails gracefully without inventing stats).
    """
    txt = (source_oracle or "").lower()
    m = _CREATURE_TOKEN_RE.search(txt)

    # defaults (fail gracefully)
    pwr, tgh = 0, 0
    types = {"Creature"}
    subtypes = set()
    keywords = set()
    tapped_on_entry = "tapped" in txt and "create" in txt

    if m:
        try:
            pwr = int(m.group(1))
            tgh = int(m.group(2))
        except Exception:
            pwr, tgh = 0, 0

        desc = (m.group(3) or "").strip().lower()

        if "artifact" in desc:
            types.add("Artifact")
        if "enchantment" in desc:
            types.add("Enchantment")

        # naive subtype scrape before "creature"
        words = re.split(r"\s+", desc)
        if "creature" in words:
            i = words.index("creature")
            for w in words[:i]:
                if w in {"white", "blue", "black", "red", "green", "colorless", "artifact", "enchantment", "token"}:
                    continue
                subtypes.add(w.capitalize())

        for k, K in _WITH_KW.items():
            if f"with {k}" in txt:
                keywords.add(K)

        tapped_on_entry = "tapped" in txt and "create" in txt

    new_pid = st.add_permanent("Creature Token", entered_turn=st.turn, face=0, is_card=False, qty=count)
    tp = st.battlefield[new_pid]
    tp.types = set(types)
    tp.subtypes = set(subtypes)
    tp.keywords = set(keywords)
    tp.base_power = pwr
    tp.base_toughness = tgh
    tp.power = pwr
    tp.toughness = tgh
    tp.tapped = bool(tapped_on_entry)
    tp.sick = True


# ---------------------------
# Creature-tap + burst mana pools
# ---------------------------

def has_creature_tap_mana_enabler(st: GameState, idx: CardIndex) -> bool:
    """True if any permanent grants creatures a tap-for-mana ability."""
    for p in st.iter_permanents():
        if "CreatureTapManaEnabler" in idx.roles_for_perm(p):
            return True
    return False


def compute_creature_tap_mana_pool(st: GameState, idx: CardIndex) -> List[Tuple[int, int, int]]:
    """
    Return a list of (power, pid, available_qty) for 1-mana taps.

    - If a CreatureTapManaEnabler is present: any untapped creature can tap for 1.
    - Otherwise: only untapped ManaDorks can tap for 1.

    Sorted by power ascending so we tap low-power bodies first.
    """
    blanket = has_creature_tap_mana_enabler(st, idx)

    pool: List[Tuple[int, int, int]] = []
    for pid, p in st.battlefield.items():
        if not idx.is_creature_perm(p):
            continue
        if p.tapped:
            continue
        if not p.can_tap(st):
            continue

        roles = idx.roles_for_perm(p)
        if "BurstManaFromCreatures" in roles:
            continue
        if (not blanket) and ("ManaDork" not in roles):
            continue

        f = idx.facts(p.name)
        if f and f.power is not None:
            try:
                pw = int(f.power)
            except Exception:
                pw = p.power_int()
        else:
            pw = p.power_int()

        pool.append((max(0, pw), pid, p.qty))

    pool.sort(key=lambda x: x[0])
    return pool


def compute_burst_mana_pools(st: GameState, idx: CardIndex) -> Tuple[List[Tuple[int, int, int]], List[Tuple[int, int, int]]]:
    """
    Return (land_like_sources, creature_sources) for BurstManaFromCreatures.

    Each entry: (mana_amount, pid, available_qty).
    Amount computed from oracle text:
      - "for each creature you control" => X = total creatures
      - "other creatures you control" => X = total creatures - source_qty
    """
    total_creatures = 0
    for p in st.iter_permanents():
        if idx.is_creature_perm(p):
            total_creatures += p.qty

    land_sources: List[Tuple[int, int, int]] = []
    creature_sources: List[Tuple[int, int, int]] = []

    for pid, p in st.battlefield.items():
        if p.tapped:
            continue
        if "BurstManaFromCreatures" not in idx.roles_for_perm(p):
            continue

        if idx.is_creature_perm(p) and not p.can_tap(st):
            continue

        txt = (idx.oracle_for_perm(p) or "").lower()
        x = total_creatures
        if "other creatures you control" in txt:
            x = max(0, total_creatures - p.qty)

        if x <= 0:
            continue

        if idx.is_creature_perm(p):
            creature_sources.append((x, pid, p.qty))
        else:
            land_sources.append((x, pid, p.qty))

    land_sources.sort(key=lambda t: t[0], reverse=True)
    creature_sources.sort(key=lambda t: t[0], reverse=True)
    return land_sources, creature_sources


# ---------------------------
# Default cast policy
# ---------------------------

def default_cast_policy(
        st: GameState,
        idx: CardIndex,
        available_mana: int,
        tap_creature_pool: List[Tuple[int, int, int]],
        burst_land_sources: List[Tuple[int, int, int]],
        burst_creature_sources: List[Tuple[int, int, int]],
) -> Tuple[int, List[Tuple[int, int, int]], List[Tuple[int, int, int]], List[Tuple[int, int, int]]]:
    """Generic casting policy with creature-tap + burst mana."""

    def has_role_name(name: str, role: str) -> bool:
        return role in idx.roles(name)

    def mana_now() -> int:
        return (
                available_mana
                + sum(qty for _pw, _pid, qty in tap_creature_pool)
                + sum(x * qty for x, _pid, qty in burst_land_sources)
                + sum(x * qty for x, _pid, qty in burst_creature_sources)
        )

    def eff_cost(card: str, mana_total: int) -> int:
        if _is_x10_haste_pump_finisher(idx, card):
            return 12 if mana_total >= 12 else 10_000
        return int(idx.mv(card))

    def pay(cost: int) -> bool:
        nonlocal available_mana, tap_creature_pool, burst_land_sources, burst_creature_sources

        # Tap burst sources first
        while available_mana < cost and (burst_land_sources or burst_creature_sources):
            if burst_land_sources:
                x, pid, qty = burst_land_sources[0]
                _tap_units(st, pid, 1)
                available_mana += x
                st.burst_lands_tapped += 1
                qty -= 1
                if qty <= 0:
                    burst_land_sources.pop(0)
                else:
                    burst_land_sources[0] = (x, pid, qty)
                continue

            if burst_creature_sources:
                x, pid, qty = burst_creature_sources[0]
                _tap_units(st, pid, 1)
                available_mana += x
                st.burst_creatures_tapped += 1
                qty -= 1
                if qty <= 0:
                    burst_creature_sources.pop(0)
                else:
                    burst_creature_sources[0] = (x, pid, qty)
                continue

        if cost <= available_mana:
            available_mana -= cost
            return True

        need = cost - available_mana
        available_mana = 0

        # Tap 1-mana creatures (low power first)
        i = 0
        while need > 0 and i < len(tap_creature_pool):
            pw, pid, qty = tap_creature_pool[i]
            _tap_units(st, pid, 1)
            st.creatures_tapped_for_mana += 1
            need -= 1
            qty -= 1
            if qty <= 0:
                tap_creature_pool.pop(i)
            else:
                tap_creature_pool[i] = (pw, pid, qty)

        return need <= 0

    while True:
        total = mana_now()
        castable = [c for c in st.hand if (not idx.is_land(c)) and eff_cost(c, total) <= total]
        if not castable:
            break

        def prio(c: str):
            if has_role_name(c, "Ramp"):
                return (1, idx.mv(c))
            if has_role_name(c, "DrawEngine"):
                return (2, idx.mv(c))
            if has_role_name(c, "Refill"):
                return (3, idx.mv(c))
            if has_role_name(c, "TokenMaker") or has_role_name(c, "TokenBurst"):
                return (4, idx.mv(c))
            if _is_finisher_like(idx, c):
                return (5, idx.mv(c))
            if has_role_name(c, "Wincon"):
                return (6, idx.mv(c))
            return (9, idx.mv(c))

        castable.sort(key=prio)
        c = castable[0]
        cost = eff_cost(c, total)

        if not pay(cost):
            break

        st.hand.remove(c)

        f = idx.facts(c)
        is_perm = bool(f and not (f.is_instant or f.is_sorcery))

        new_perm: Permanent | None = None
        if is_perm:
            new_pid = st.add_permanent(c, entered_turn=st.turn, face=0, is_card=True, qty=1)
            new_perm = st.battlefield.get(new_pid)

        # Ramp: only increment static sources for non-dorks, non-enablers, non-burst.
        if has_role_name(c, "Ramp"):
            roles = idx.roles_for_perm(new_perm) if new_perm else idx.roles(c)
            if ("ManaDork" not in roles) and ("CreatureTapManaEnabler" not in roles) and ("BurstManaFromCreatures" not in roles):
                st.ramp_sources_in_play += 1
                if new_perm is not None and idx.is_artifact_perm(new_perm) and (not idx.is_creature_perm(new_perm)) and (not idx.is_land_perm(new_perm)):
                    available_mana += 1

        # Refill
        if has_role_name(c, "Refill"):
            st.refills_resolved += 1
            for _ in range(2):
                if st.library:
                    st.hand.append(st.library.pop(0))

        # Tokens (legacy: count + generic token creation)
        if (has_role_name(c, "TokenBurst") or has_role_name(c, "TokenMaker")) and f:
            created = estimate_tokens_created_from_text(f.oracle_text)
            if created > 0:
                _add_generic_creature_tokens(st, f.oracle_text, created)

        # Finisher effects
        if _is_x10_haste_pump_finisher(idx, c):
            st.finisher_boost = max(st.finisher_boost, 10)
            st.finisher_haste = True

        if _is_greatest_power_trample_pump(idx, c):
            x = st.max_creature_power()
            st.finisher_boost = max(st.finisher_boost, x)
            st.finisher_trample = True

        if has_role_name(c, "Finisher") and f and (f.is_instant or f.is_sorcery):
            st.finisher_boost = max(st.finisher_boost, 3)

    return available_mana, tap_creature_pool, burst_land_sources, burst_creature_sources
