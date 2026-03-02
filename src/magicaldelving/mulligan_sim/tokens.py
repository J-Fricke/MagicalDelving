from __future__ import annotations

import re

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def estimate_tokens_created_from_text(txt: str) -> int:
    t = (txt or "").lower()
    if "create" not in t or "token" not in t:
        return 0

    m = re.search(r"create\s+(\d+)\s+.*token", t)
    if m:
        try:
            return max(1, int(m.group(1)))
        except Exception:
            return 1

    m = re.search(r"create\s+(one|two|three|four|five|six|seven|eight|nine|ten)\s+.*token", t)
    if m:
        return _WORD_NUM.get(m.group(1), 1)

    if re.search(r"create\s+x\s+.*token", t):
        return 4

    return 1