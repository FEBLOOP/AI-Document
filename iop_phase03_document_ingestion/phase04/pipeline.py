from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from . import SCHEMA_VERSION
from .config import Settings
from .embeddings import create_embedder
from .metadata import extract_metadata
from .normalization import normalize_text
from .sections import detect_section


def _document_id(data: dict[str, Any]) -> str:
    if data.get("document_id"): return str(data["document_id"])
    raw = str(data.get("source_file") or data.get("filename") or "unknown")
    return "DOC_" + hashlib.sha256(raw.encode()).hexdigest()[:12].upper()


def _pages(data: Any) -> list[dict[str, Any]]:
    return data if isinstance(data, list) else data.get("pages", [])


def _blocks(data: Any) -> list[dict[str, Any]]:
    pages = _pages(data)
    result = []
    for page_index, page in enumerate(pages, 1):
        page_no = page.get("page", page.get("page_number", page_index))
        blocks = page.get("blocks") or [{"block_id": f"P{page_no}_TEXT_1", "text": page.get("text", "")}]
        for block_index, block in enumerate(blocks, 1):
            text = str(block.get("text", ""))
            if not text.strip(): continue
            result.append({"block_id": block.get("block_id", f"P{page_no}_B{block_index}"), "page": page_no,
                           "text": text, "normalized_text": normalize_text(text), "bbox": block.get("bbox"),
                           "confidence": block.get("confidence"), "ocr": block.get("ocr", page.get("ocr"))})
    return result


def build_phase04(data: Any, settings: Settings, embed: bool = False) -> dict[str, Any]:
    root = data if isinstance(data, dict) else {}
    document_id = _document_id(root)
    current_section = None
    chunks: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    size = 0

    def flush() -> None:
        nonlocal pending, size
        if not pending: return
        index = len(chunks) + 1
        original = "\n".join(x["text"] for x in pending)
        normalized = "\n".join(x["normalized_text"] for x in pending)
        provenance = [{k: x.get(k) for k in ("block_id", "page", "bbox", "confidence", "ocr")} for x in pending]
        inherited_metadata = dict(root.get("metadata", {}))
        if root.get("language") and not inherited_metadata.get("language"):
            inherited_metadata["language"] = root["language"]
        metadata = {"source_file": root.get("source_file") or root.get("filename"),
                    "ocr": any(x.get("ocr") is not False for x in pending), "ingestion_metadata": root.get("metadata", {})}
        metadata.update(extract_metadata(normalized, inherited_metadata))
        chunk_id = f"{document_id}_C{index:04d}"
        chunks.append({"schema_version": SCHEMA_VERSION, "chunk_id": chunk_id, "document_id": document_id,
                       "text": original, "normalized_text": normalized, "page_start": min(x["page"] for x in pending),
                       "page_end": max(x["page"] for x in pending), "source_blocks": provenance,
                       "section": current_section, "metadata": metadata,
                       "llm_ready": {"content": normalized, "evidence_id": chunk_id,
                                     "citation": {"page_start": min(x["page"] for x in pending), "page_end": max(x["page"] for x in pending),
                                                  "source_block_ids": [x["block_id"] for x in pending]}}})
        overlap = pending[-settings.chunk_overlap_blocks:] if settings.chunk_overlap_blocks else []
        pending, size = overlap, sum(len(x["normalized_text"]) for x in overlap)

    for block in _blocks(data):
        heading = detect_section(block["normalized_text"], settings.min_section_heading_chars)
        if heading:
            flush()
            current_section = heading
        length = len(block["normalized_text"])
        if pending and size + length > settings.chunk_max_chars:
            flush()
        pending.append(block); size += length
    flush()
    if embed:
        if settings.embedding_provider == "none": raise ValueError("Embedding is enabled but embedding_provider is 'none'.")
        vectors = create_embedder(settings.embedding_provider, settings.embedding_model, settings.embedding_dimensions).embed([c["normalized_text"] for c in chunks])
        for chunk, vector in zip(chunks, vectors):
            chunk["embedding"] = {"provider": settings.embedding_provider, "model": settings.embedding_model, "vector": vector}
    return {"schema_version": SCHEMA_VERSION, "document_id": document_id, "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {"source_file": root.get("source_file") or root.get("filename"), "phase": "04-document-understanding"}, "chunks": chunks}
