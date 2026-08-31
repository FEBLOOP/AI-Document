from __future__ import annotations

import re
from collections import Counter
from typing import Any

EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
URL = re.compile(r"https?://[^\s)>]+", re.I)
ISO_DATE = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b")
THAI_DATE = re.compile(r"\b\d{1,2}\s+(?:มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+\d{4}\b")


def extract_metadata(text: str, inherited: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract conservative, retrieval-useful metadata without an LLM."""
    inherited = inherited or {}
    thai = len(re.findall(r"[\u0E00-\u0E7F]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    language = inherited.get("language") or ("tha+eng" if thai and latin else "tha" if thai else "eng" if latin else "unknown")
    dates = list(dict.fromkeys(ISO_DATE.findall(text) + THAI_DATE.findall(text)))
    return {
        "language": language,
        "emails": sorted(set(EMAIL.findall(text))),
        "urls": sorted(set(URL.findall(text))),
        "dates": dates,
    }
