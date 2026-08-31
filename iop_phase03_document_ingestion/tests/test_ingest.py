from types import SimpleNamespace
from app.ingest import needs_ocr
from app.ocr import build_ocr_blocks, normalize_ocr_text

def page(chars): return SimpleNamespace(text_length=chars)

def test_native_text_pdf():
    assert needs_ocr([page(100), page(200), page(150)]) is False

def test_scanned_pdf():
    assert needs_ocr([page(0), page(10), page(0)]) is True

def test_thai_normalization_preserves_english_spaces():
    assert normalize_ocr_text("อ ง ค ์ ก า ร THE ZOO") == "องค์การ THE ZOO"

def test_ocr_blocks_follow_tesseract_block_positions():
    data = {"text": ["หัว", "ข้อ", "Body", "text"], "conf": ["90", "90", "80", "80"],
            "left": [10, 35, 10, 50], "top": [10, 10, 80, 80], "width": [20, 20, 35, 35],
            "height": [15, 15, 15, 15], "block_num": [1, 1, 2, 2], "par_num": [1, 1, 1, 1],
            "line_num": [1, 1, 1, 1]}
    blocks = build_ocr_blocks(data, 1)
    assert [block.text for block in blocks] == ["หัวข้อ", "Body text"]
    assert blocks[1].bbox == [10, 80, 85, 95]
