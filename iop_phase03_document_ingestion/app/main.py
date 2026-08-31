import argparse
from .ingest import ingest_pdf

def main():
    p = argparse.ArgumentParser(description="IOP Phase 03 - Document Ingestion")
    p.add_argument("pdf")
    p.add_argument("--output", default="data/output")
    p.add_argument("--force-ocr", action="store_true")
    p.add_argument("--ocr-lang", default="tha+eng")
    p.add_argument("--ocr-dpi", type=int, default=300)
    p.add_argument("--ocr-psm", type=int, choices=(3, 6), default=3)
    p.add_argument("--ocr-preprocess", choices=("auto", "none", "binary"), default="auto")
    args = p.parse_args()
    output, result = ingest_pdf(args.pdf, args.output, args.force_ocr, args.ocr_lang, args.ocr_dpi,
                                args.ocr_psm, args.ocr_preprocess)
    print(f"Document ID : {result['document_id']}")
    print(f"Pages       : {result['page_count']}")
    print(f"OCR used    : {result['ocr_used']}")
    print(f"Output      : {output}")

if __name__ == "__main__":
    main()
