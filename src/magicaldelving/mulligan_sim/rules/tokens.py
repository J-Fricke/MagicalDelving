from __future__ import annotations

import re

# NOTE: Back-compat module. TODO: replace call sites with deterministic token resolution.
_WORD_NUM = {
    "a": 1, "an": 1,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
}

def estimate_tokens_created_from_text(txt: str, *, default_x: int = 4) -> int:
    t = (txt or "").lower()
    if "create" not in t or "token" not in t:
        return 0

    total = 0

    for m in re.finditer(r"\bcreate\s+(\d+)\b[^\.]*?\btoken\b", t):
        try:
            total += max(0, int(m.group(1)))
        except Exception:
            total += 1

    for m in re.finditer(
            r"\bcreate\s+(a|an|one|two|three|four|five|six|seven|eight|nine|ten|"
            r"eleven|twelve|thirteen|fourteen|fifteen)\b[^\.]*?\btoken\b",
            t,
    ):
        total += _WORD_NUM.get(m.group(1), 1)

    for _m in re.finditer(r"\bcreate\s+x\b[^\.]*?\btoken\b", t):
        total += max(0, int(default_x))

    return total or 1
