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
    sort_keys: bool = False,
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

    def _j_action(action: Dict[str, Any]) -> str:
        # Ensure "kind" is first for readability, keep the rest in insertion order.
        if "kind" in action:
            ordered: Dict[str, Any] = {"kind": action.get("kind")}
            for k, v in action.items():
                if k == "kind":
                    continue
                ordered[k] = v
            action = ordered
        return json.dumps(action, ensure_ascii=False, sort_keys=False, separators=(",", ": "))

    def _write_pretty_kv(level: int, k: str, v: Any, *, comma: bool) -> None:
        dump = json.dumps(v, ensure_ascii=False, sort_keys=False, indent=indent)
        lines = dump.splitlines() or [dump]
        for li, line in enumerate(lines):
            is_last = li == len(lines) - 1
            trailing = "," if (comma and is_last) else ""
            if li == 0:
                fp.write(f"{_indent(level)}{json.dumps(k)}: {line}{trailing}\n")
            else:
                fp.write(f"{_indent(level)}{line}{trailing}\n")

    def _write_kv(level: int, k: str, v: Any, *, comma: bool) -> None:
        fp.write(f"{_indent(level)}{json.dumps(k)}: {_j(v)}")
        fp.write(",\n" if comma else "\n")

    def _ordered_phase_keys(turn_obj: Dict[str, Any]) -> List[str]:
        keys = [k for k in turn_obj.keys() if k not in {"start_state", "end_state"}]
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

                # Start state (pretty printed)
                has_start = "start_state" in turn_obj
                has_end = "end_state" in turn_obj
                phase_keys = _ordered_phase_keys(turn_obj)
                if has_start:
                    _write_pretty_kv(4, "start_state", turn_obj.get("start_state"), comma=(len(phase_keys) > 0 or has_end))

                for pj, ph in enumerate(phase_keys):
                    actions = turn_obj.get(ph) or []
                    fp.write(f"{_indent(4)}{json.dumps(ph)}: [\n")
                    for aj, action in enumerate(actions):
                        line = _j_action(action)
                        trailing = "," if aj < len(actions) - 1 else ""
                        fp.write(f"{_indent(5)}{line}{trailing}\n")
                    fp.write(f"{_indent(4)}]")
                    fp.write(",\n" if (pj < len(phase_keys) - 1 or has_end) else "\n")

                if has_end:
                    _write_pretty_kv(4, "end_state", turn_obj.get("end_state"), comma=False)

                fp.write(f"{_indent(3)}}}")
                fp.write(",\n" if ti < len(turns) - 1 else "\n")

            fp.write(f"{_indent(2)}]\n")

        fp.write(f"{_indent(1)}}}")
        fp.write(",\n" if i < len(replays) - 1 else "\n")

    fp.write("]\n")
