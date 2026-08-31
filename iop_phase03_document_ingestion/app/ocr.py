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

    physical_blocks: dict[int, list[tuple[int, list[dict]]]] = defaultdict(list)
    for (block_number, paragraph_number, line_number), words in grouped.items():
        physical_blocks[block_number].append((paragraph_number * 10000 + line_number, words))

    blocks: list[TextBlock] = []
    ordered_blocks = sorted(physical_blocks.items(), key=lambda item: min(_bbox(words)[1] for _, words in item[1]))
    for output_number, (_, lines) in enumerate(ordered_blocks, start=1):
        ordered_lines = sorted(lines, key=lambda item: (_bbox(item[1])[1], _bbox(item[1])[0], item[0]))
        line_text = [normalize_ocr_text(" ".join(word["text"] for word in words)) for _, words in ordered_lines]
        words = [word for _, line_words in ordered_lines for word in line_words]
        text = "\n".join(line for line in line_text if line).strip()
        if text:
            blocks.append(TextBlock(page=page_index, block_id=f"P{page_index}_OCR_{output_number}", text=text,
                                    source="ocr", confidence=_word_confidence(words), bbox=_bbox(words)))
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
