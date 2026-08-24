import io
from pathlib import Path

import fitz
import pytesseract
from PIL import Image

from .models import DocumentPage, TextBlock


TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

if Path(TESSERACT_PATH).exists():
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH
else:
    raise FileNotFoundError(
        f"ไม่พบ Tesseract ที่: {TESSERACT_PATH}"
    )


def ocr_pdf(
    pdf_path: str,
    dpi: int = 200,
    lang: str = "tha+eng",
) -> list[DocumentPage]:

    pages = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    with fitz.open(pdf_path) as doc:
        for page_index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png")))

            data = pytesseract.image_to_data(
                image,
                lang=lang,
                output_type=pytesseract.Output.DICT,
            )

            words = []
            confidences = []

            for i, raw_text in enumerate(data["text"]):
                text = raw_text.strip()
                if not text:
                    continue

                try:
                    confidence = float(data["conf"][i])
                except (ValueError, TypeError):
                    confidence = -1.0

                words.append(text)

                if confidence >= 0:
                    confidences.append(confidence)

            page_text = " ".join(words).strip()

            avg_conf = (
                sum(confidences) / len(confidences)
                if confidences
                else None
            )

            blocks = []

            if page_text:
                blocks.append(
                    TextBlock(
                        page=page_index,
                        block_id=f"P{page_index}_OCR_1",
                        text=page_text,
                        source="ocr",
                        confidence=avg_conf,
                    )
                )

            pages.append(
                DocumentPage(
                    page=page_index,
                    text=page_text,
                    extraction_method="ocr",
                    ocr_used=True,
                    text_length=len(page_text),
                    blocks=blocks,
                )
            )

    return pages