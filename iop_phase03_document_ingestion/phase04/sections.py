from __future__ import annotations

import re

HEADING_PREFIX = re.compile(r"^(?:(?:\d+(?:\.\d+)*|[A-Z])\s*[.)]|บทที่\s*\d+)\s*", re.I)


def detect_section(text: str, minimum_chars: int = 3) -> str | None:
    """Conservative heading detector; returns a section label or None."""
    line = text.strip().split("\n", 1)[0].strip()
    if len(line) < minimum_chars or len(line) > 140 or line.endswith((".", ";", "?", "!")):
        return None
    words = line.split()
    has_prefix = bool(HEADING_PREFIX.match(line))
    compact = len(words) <= 14
    letters = sum(c.isalpha() or "\u0E00" <= c <= "\u0E7F" for c in line)
    looks_title = has_prefix or (compact and letters >= minimum_chars and not re.search(r"[,:;]", line))
    return line if looks_title else None
