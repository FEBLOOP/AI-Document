from dataclasses import dataclass, asdict
from typing import Optional

@dataclass
class TextBlock:
    page: int
    block_id: str
    text: str
    source: str
    confidence: Optional[float] = None
    bbox: Optional[list[float]] = None

    def to_dict(self):
        return asdict(self)

@dataclass
class DocumentPage:
    page: int
    text: str
    extraction_method: str
    ocr_used: bool
    text_length: int
    blocks: list[TextBlock]

    def to_dict(self):
        return {
            "page": self.page,
            "text": self.text,
            "extraction_method": self.extraction_method,
            "ocr_used": self.ocr_used,
            "text_length": self.text_length,
            "blocks": [b.to_dict() for b in self.blocks],
        }
