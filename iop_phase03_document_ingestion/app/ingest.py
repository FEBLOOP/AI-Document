from pathlib import Path
from datetime import datetime, timezone
import hashlib, json
from .pdf_text import extract_text_from_pdf
from .ocr import ocr_pdf

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def needs_ocr(pages, min_chars: int = 30, min_text_pages_ratio: float = 0.60) -> bool:
    if not pages:
        return True
    usable = sum(p.text_length >= min_chars for p in pages)
    return usable / len(pages) < min_text_pages_ratio

def ingest_pdf(pdf_path: str, output_dir: str = "data/output", force_ocr: bool = False,
               ocr_lang: str = "tha+eng", ocr_dpi: int = 300, ocr_psm: int = 3,
               ocr_preprocess: str = "auto"):
    pdf = Path(pdf_path)
    if not pdf.exists():
        raise FileNotFoundError(f"ไม่พบไฟล์: {pdf}")
    if pdf.suffix.lower() != ".pdf":
        raise ValueError("Phase 03 รองรับ PDF เป็น input หลัก")
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    file_hash = sha256_file(pdf)
    document_id = f"DOC_{file_hash[:12].upper()}"
    native = extract_text_from_pdf(str(pdf))
    use_ocr = force_ocr or needs_ocr(native)
    pages = ocr_pdf(str(pdf), ocr_dpi, ocr_lang, ocr_psm, ocr_preprocess) if use_ocr else native
    result = {
        "schema_version": "1.0",
        "document_id": document_id,
        "source_file": pdf.name,
        "file_type": "pdf",
        "file_size_bytes": pdf.stat().st_size,
        "file_sha256": file_hash,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "ocr_used": use_ocr,
        "ocr_language": ocr_lang if use_ocr else None,
        "ocr_dpi": ocr_dpi if use_ocr else None,
        "ocr_psm": ocr_psm if use_ocr else None,
        "ocr_preprocess": ocr_preprocess if use_ocr else None,
        "page_count": len(pages),
        "pages": [p.to_dict() for p in pages],
    }
    output_file = out / f"{document_id}.json"
    output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_file, result
