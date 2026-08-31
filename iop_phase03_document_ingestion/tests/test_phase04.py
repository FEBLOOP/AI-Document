from phase04.config import Settings
from phase04.normalization import normalize_text
from phase04.pipeline import build_phase04


def test_thai_spaced_ocr_is_joined_without_joining_english():
    assert normalize_text("ก า ร จ ั ด ก า ร  AI 2026") == "การจัดการ AI 2026"
    assert normalize_text("การ จัดการ knowledge") == "การ จัดการ knowledge"


def test_chunks_keep_phase03_provenance_and_embedding():
    source = {"document_id": "DOC_TEST", "filename": "example.pdf", "metadata": {"language": "tha+eng"}, "pages": [
        {"page": 3, "ocr": True, "blocks": [
            {"block_id": "P3_OCR_1", "text": "1. นโยบาย นวัตกรรม", "bbox": [1, 2, 3, 4], "confidence": 92.4},
            {"block_id": "P3_OCR_2", "text": "ก า ร จ ั ด ก า ร ความรู้"}]}]}
    output = build_phase04(source, Settings(embedding_provider="hash", embedding_dimensions=8), embed=True)
    chunk = output["chunks"][0]
    assert output["document_id"] == "DOC_TEST"
    assert chunk["page_start"] == 3 and chunk["source_blocks"][0]["bbox"] == [1, 2, 3, 4]
    assert "การจัดการ" in chunk["normalized_text"]
    assert len(chunk["embedding"]["vector"]) == 8
    assert chunk["llm_ready"]["citation"]["source_block_ids"] == ["P3_OCR_1", "P3_OCR_2"]
