from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, TextIO


# Keep phases in a human-friendly order when printing.
_DEFAULT_PHASE_ORDER: Sequence[str] = (
    "MULLIGAN",
    "BEGINNING",
    "MAIN1",
    "COMBAT",
    "MAIN2",
    "END",
    "CLEANUP",
)


def dump_replays(
    replays: List[Dict[str, Any]],
    fp: TextIO,
    *,
    indent: int = 2,
    sort_keys: bool = True,
    phase_order: Optional[Sequence[str]] = None,
) -> None:
    """Pretty-print replay JSON with compact (single-line) action entries.

    The overall structure matches a normal json.dumps(indent=2) output, but any phase's
    action list is rendered so that each action dict is a single line.
    """

    if phase_order is None:
        phase_order = _DEFAULT_PHASE_ORDER

    def _indent(level: int) -> str:
        return " " * (indent * level)

    def _j(v: Any) -> str:
        # Match Python's indent-mode separators: comma w/ no space, colon w/ space.
        return json.dumps(v, ensure_ascii=False, sort_keys=sort_keys, separators=(",", ": "))

    def _write_kv(level: int, k: str, v: Any, *, comma: bool) -> None:
        fp.write(f"{_indent(level)}{json.dumps(k)}: {_j(v)}")
        fp.write(",\n" if comma else "\n")

    def _ordered_phase_keys(turn_obj: Dict[str, Any]) -> List[str]:
        keys = [k for k in turn_obj.keys() if k not in {"turn", "state"}]
        ordered: List[str] = []
        for ph in phase_order:
            if ph in keys:
                ordered.append(ph)
                keys.remove(ph)
        # Any unexpected phases get appended deterministically.
        ordered.extend(sorted(keys))
        return ordered

    fp.write("[\n")
    for i, replay in enumerate(replays):
        fp.write(f"{_indent(1)}{{\n")

        # Stable, human-first ordering for replay metadata.
        base_keys = ["trial", "win_turn", "win_reason", "cumulative_damage", "turns"]
        extra_keys = [k for k in replay.keys() if k not in base_keys]
        key_order = base_keys + (sorted(extra_keys) if sort_keys else extra_keys)

        # Print everything except turns first.
        for k in key_order:
            if k == "turns":
                continue
            if k not in replay:
                continue
            # If turns exists, we're not the last key.
            comma = any(kk in replay for kk in key_order[key_order.index(k) + 1 :])
            _write_kv(2, k, replay[k], comma=comma)

        # Turns list (with phase action lists compacted)
        if "turns" in replay:
            fp.write(f"{_indent(2)}\"turns\": [\n")
            turns = replay.get("turns") or []
            for ti, turn_obj in enumerate(turns):
                fp.write(f"{_indent(3)}{{\n")

                # Turn number first
                has_state = "state" in turn_obj
                has_more_after_turn = (len(turn_obj.keys()) > 1)
                _write_kv(4, "turn", turn_obj.get("turn"), comma=has_more_after_turn)

                # Optional state snapshot (kept compact on a single line)
                if has_state:
                    # comma if there are phases after state
                    phase_keys_for_comma = _ordered_phase_keys(turn_obj)
                    _write_kv(4, "state", turn_obj.get("state"), comma=(len(phase_keys_for_comma) > 0))

                phase_keys = _ordered_phase_keys(turn_obj)
                for pj, ph in enumerate(phase_keys):
                    actions = turn_obj.get(ph) or []
                    fp.write(f"{_indent(4)}{json.dumps(ph)}: [\n")
                    for aj, action in enumerate(actions):
                        line = _j(action)
                        trailing = "," if aj < len(actions) - 1 else ""
                        fp.write(f"{_indent(5)}{line}{trailing}\n")
                    fp.write(f"{_indent(4)}]")
                    fp.write(",\n" if pj < len(phase_keys) - 1 else "\n")

                fp.write(f"{_indent(3)}}}")
                fp.write(",\n" if ti < len(turns) - 1 else "\n")

            fp.write(f"{_indent(2)}]\n")

        fp.write(f"{_indent(1)}}}")
        fp.write(",\n" if i < len(replays) - 1 else "\n")

    fp.write("]\n")
