import io
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import pymupdf
import pytesseract
from PIL import Image, ImageFilter, ImageOps

from .models import DocumentPage, TextBlock

DEFAULT_TESSERACT_PATH = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
THAI_CHARACTER = r"\u0E00-\u0E7F"


def configure_tesseract(tesseract_path: str | Path | None = None) -> None:
    """Configure pytesseract for Windows without requiring a PATH change."""
    executable = Path(tesseract_path) if tesseract_path else DEFAULT_TESSERACT_PATH
    if executable.exists():
        pytesseract.pytesseract.tesseract_cmd = str(executable)
    elif tesseract_path:
        raise FileNotFoundError(f"ไม่พบ Tesseract ที่: {executable}")


configure_tesseract()


def preprocess_image(image: Image.Image, mode: str = "auto") -> Image.Image:
    """Prepare a rendered page for OCR while retaining its original layout."""
    if mode not in {"auto", "none", "binary"}:
        raise ValueError("ocr_preprocess ต้องเป็น auto, none หรือ binary")
    image = ImageOps.exif_transpose(image).convert("L")
    if mode == "none":
        return image
    image = ImageOps.autocontrast(image, cutoff=1).filter(ImageFilter.MedianFilter(size=3))
    if mode == "binary":
        return image.point(lambda value: 255 if value >= 180 else 0, mode="1")
    return image


def normalize_ocr_text(text: str) -> str:
    """Remove artificial spacing between Thai glyphs without changing English words."""
    text = unicodedata.normalize("NFC", text).replace("\u200b", "")
    text = re.sub(rf"(?<=[{THAI_CHARACTER}])\s+(?=[{THAI_CHARACTER}])", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _word_confidence(words: Iterable[dict]) -> float | None:
    values = [item["confidence"] for item in words if item["confidence"] >= 0]
    return sum(values) / len(values) if values else None


def _bbox(words: Iterable[dict]) -> list[float]:
    items = list(words)
    return [min(item["bbox"][0] for item in items), min(item["bbox"][1] for item in items),
            max(item["bbox"][2] for item in items), max(item["bbox"][3] for item in items)]


def _horizontal_overlap(left: list[float], right: list[float]) -> float:
    """Return the horizontal overlap between two bounding boxes."""
    return max(0.0, min(left[2], right[2]) - max(left[0], right[0]))


def _column_order(blocks: list[dict]) -> list[dict]:
    """Read overlapping x-ranges as columns, from left to right.

    Wide blocks are treated as row separators so a page-wide heading does not
    accidentally join the left and right columns into one connected column.
    """
    if len(blocks) < 2:
        return blocks

    page_left = min(block["bbox"][0] for block in blocks)
    page_right = max(block["bbox"][2] for block in blocks)
    page_width = max(1.0, page_right - page_left)
    spanning = [
        block for block in blocks
        if block["bbox"][2] - block["bbox"][0] >= page_width * 0.75
    ]
    regular = [block for block in blocks if block not in spanning]

    # Page-wide headings and footers split the page into independent horizontal
    # bands. Column detection is run separately inside each band.
    spanning.sort(key=lambda block: (block["bbox"][1], block["bbox"][0]))
    bands: list[list[dict]] = [[] for _ in range(len(spanning) + 1)]
    for block in regular:
        center_y = (block["bbox"][1] + block["bbox"][3]) / 2
        band_index = 0
        while (
            band_index < len(spanning)
            and center_y >= (spanning[band_index]["bbox"][1] + spanning[band_index]["bbox"][3]) / 2
        ):
            band_index += 1
        bands[band_index].append(block)

    ordered: list[dict] = []
    for band_index, band in enumerate(bands):
        if band:
            # Connected components by x-overlap represent visual columns.
            remaining = set(range(len(band)))
            columns: list[list[dict]] = []
            while remaining:
                seed = remaining.pop()
                component = {seed}
                pending = [seed]
                while pending:
                    current = pending.pop()
                    neighbours = {
                        candidate
                        for candidate in remaining
                        if _horizontal_overlap(
                            band[current]["bbox"], band[candidate]["bbox"]
                        ) > 0
                    }
                    remaining.difference_update(neighbours)
                    component.update(neighbours)
                    pending.extend(neighbours)
                columns.append([band[index] for index in component])

            columns.sort(key=lambda column: min(block["bbox"][0] for block in column))
            for column in columns:
                ordered.extend(
                    sorted(column, key=lambda block: (block["bbox"][1], block["bbox"][0]))
                )
        if band_index < len(spanning):
            ordered.append(spanning[band_index])
    return ordered


def _starts_new_item(text: str) -> bool:
    return bool(re.match(r"^(?:[•●▪◦\-–—]|\(?\d+[.)]|[ก-ฮ][.)])\s*", text))


def _should_merge_lines(previous: dict, current: dict) -> bool:
    """Join close wrapped lines only inside the same OCR paragraph."""
    if previous["paragraph"] != current["paragraph"]:
        return False
    if re.search(r"[.!?。！？…]$", previous["text"]):
        return False
    if _starts_new_item(current["text"]):
        return False

    previous_height = max(1.0, previous["bbox"][3] - previous["bbox"][1])
    current_height = max(1.0, current["bbox"][3] - current["bbox"][1])
    line_height = min(previous_height, current_height)
    vertical_gap = current["bbox"][1] - previous["bbox"][3]
    vertical_advance = current["bbox"][1] - previous["bbox"][1]
    return vertical_advance >= line_height * 0.4 and vertical_gap <= line_height * 0.8


def _block_text(lines: list[dict]) -> str:
    paragraphs: list[str] = []
    previous: dict | None = None
    for line in lines:
        if previous is not None and _should_merge_lines(previous, line):
            paragraphs[-1] = f"{paragraphs[-1]} {line['text']}"
        else:
            paragraphs.append(line["text"])
        previous = line
    return "\n".join(paragraphs).strip()


def build_ocr_blocks(data: dict, page_index: int) -> list[TextBlock]:
    """Build physical blocks using Tesseract's block and line coordinates."""
    grouped: dict[tuple[int, int, int], list[dict]] = defaultdict(list)
    for index, raw_text in enumerate(data["text"]):
        text = raw_text.strip()
        if not text:
            continue
        try:
            confidence = float(data["conf"][index])
        except (ValueError, TypeError):
            confidence = -1.0
        x, y = int(data["left"][index]), int(data["top"][index])
        width, height = int(data["width"][index]), int(data["height"][index])
        paragraph_number = int(data.get("par_num", [0] * len(data["text"]))[index])
        grouped[(int(data["block_num"][index]), paragraph_number, int(data["line_num"][index]))].append(
            {"text": text, "confidence": confidence, "bbox": [x, y, x + width, y + height]}
        )

    physical_blocks: dict[int, list[dict]] = defaultdict(list)
    for (block_number, paragraph_number, line_number), words in grouped.items():
        ordered_words = sorted(words, key=lambda word: (word["bbox"][0], word["bbox"][1]))
        physical_blocks[block_number].append(
            {
                "paragraph": paragraph_number,
                "line": line_number,
                "words": ordered_words,
                "bbox": _bbox(ordered_words),
                "text": normalize_ocr_text(" ".join(word["text"] for word in ordered_words)),
            }
        )

    blocks: list[TextBlock] = []
    layout_blocks = []
    for block_number, lines in physical_blocks.items():
        words = [word for line in lines for word in line["words"]]
        layout_blocks.append(
            {"number": block_number, "lines": lines, "words": words, "bbox": _bbox(words)}
        )

    for output_number, block in enumerate(_column_order(layout_blocks), start=1):
        ordered_lines = sorted(
            block["lines"],
            key=lambda line: (
                line["bbox"][1], line["bbox"][0], line["paragraph"], line["line"]
            ),
        )
        text = _block_text(ordered_lines)
        if text:
            blocks.append(TextBlock(page=page_index, block_id=f"P{page_index}_OCR_{output_number}", text=text,
                                    source="ocr", confidence=_word_confidence(block["words"]),
                                    bbox=block["bbox"]))
    return blocks


def ocr_pdf(pdf_path: str, dpi: int = 300, lang: str = "tha+eng", psm: int = 3,
            preprocess: str = "auto") -> list[DocumentPage]:
    """OCR every PDF page at the configured resolution and segmentation mode."""
    if dpi <= 0:
        raise ValueError("ocr_dpi ต้องมากกว่า 0")
    if psm not in {3, 6}:
        raise ValueError("ocr_psm รองรับเฉพาะ 3 หรือ 6")
    zoom = dpi / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    pages = []
    with pymupdf.open(pdf_path) as doc:
        for page_index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image = preprocess_image(Image.open(io.BytesIO(pix.tobytes("png"))), preprocess)
            data = pytesseract.image_to_data(image, lang=lang, config=f"--psm {psm}",
                                              output_type=pytesseract.Output.DICT)
            blocks = build_ocr_blocks(data, page_index)
            page_text = "\n".join(block.text for block in blocks).strip()
            pages.append(DocumentPage(page=page_index, text=page_text, extraction_method="ocr",
                                      ocr_used=True, text_length=len(page_text), blocks=blocks))
    return pages
