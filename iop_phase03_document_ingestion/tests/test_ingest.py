from types import SimpleNamespace
from app.ingest import needs_ocr

def page(chars): return SimpleNamespace(text_length=chars)

def test_native_text_pdf():
    assert needs_ocr([page(100), page(200), page(150)]) is False

def test_scanned_pdf():
    assert needs_ocr([page(0), page(10), page(0)]) is True
