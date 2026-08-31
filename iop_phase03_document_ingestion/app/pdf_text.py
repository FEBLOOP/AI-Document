import pymupdf
from .models import DocumentPage, TextBlock

def extract_text_from_pdf(pdf_path: str) -> list[DocumentPage]:
    pages = []
    with pymupdf.open(pdf_path) as doc:
        for page_no, page in enumerate(doc, 1):
            blocks = []
            for block_no, block in enumerate(page.get_text("blocks"), 1):
                text = block[4].strip()
                if not text:
                    continue
                blocks.append(TextBlock(
                    page=page_no,
                    block_id=f"P{page_no}_B{block_no}",
                    text=text,
                    source="pdf_text",
                    bbox=[float(x) for x in block[:4]],
                ))
            text = "\n".join(b.text for b in blocks).strip()
            pages.append(DocumentPage(page_no, text, "pdf_text", False, len(text), blocks))
    return pages
