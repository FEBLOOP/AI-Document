from __future__ import annotations

import re
import unicodedata

THAI_RE = re.compile(r"[\u0E00-\u0E7F]")


def normalize_text(text: str) -> str:
    """Normalize whitespace/OCR noise, including Thai characters separated by spaces.

    Spaces between Thai letters/marks are removed only inside Thai runs. Spaces near
    ASCII words and numbers are left intact, avoiding accidental English word joins.
    """
    text = unicodedata.normalize("NFC", text or "")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[\t\r\f\v ]+", " ", text)
    # Only collapse a run of four or more individually spaced Thai characters.
    # The negative lookbehind avoids turning ordinary Thai word spaces such as
    # `การ จัดการ` into one word by beginning in the middle of the first word.
    spaced_thai = re.compile(r"(?<![\u0E00-\u0E7F])(?:[\u0E00-\u0E7F] ){3,}[\u0E00-\u0E7F]")
    text = spaced_thai.sub(lambda match: match.group(0).replace(" ", ""), text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
