from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ASSESSMENT_ID_RE = re.compile(r"^ASSESSMENT_[A-F0-9]{12}$")
SOURCE_ID_RE = re.compile(r"^SOURCE_\d{3}$")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_assessment_id(seed: str) -> str:
    return "ASSESSMENT_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12].upper()


def source_id(index: int) -> str:
    if index < 1 or index > 999:
        raise ValueError("Source index must be between 1 and 999.")
    return f"SOURCE_{index:03d}"


def sanitize_pdf_filename(filename: str | None) -> str:
    value = (filename or "").strip()
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or Path(value).name != value
        or Path(value).suffix.lower() != ".pdf"
    ):
        raise ValueError("ชื่อไฟล์ PDF ไม่ปลอดภัยหรือไม่ถูกต้อง")
    cleaned = "".join(character for character in value if character.isprintable()).strip()
    if not cleaned or len(cleaned) > 240:
        raise ValueError("ชื่อไฟล์ PDF ยาวเกินไปหรือไม่ถูกต้อง")
    return cleaned


def submission_fingerprint(
    sources: list[dict[str, Any]],
    config: dict[str, Any],
) -> str:
    payload = {
        "sources": sorted(
            [
                {"sha256": item["sha256"], "size": item["size_bytes"]}
                for item in sources
            ],
            key=lambda item: (item["sha256"], item["size"]),
        ),
        "config": config,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_combined_phase03(
    assessment_id: str,
    source_results: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    if not ASSESSMENT_ID_RE.fullmatch(assessment_id):
        raise ValueError("Invalid assessment_id.")
    sources = []
    total_pages = 0
    for descriptor, phase03 in source_results:
        sid = descriptor["source_id"]
        if not SOURCE_ID_RE.fullmatch(str(sid)):
            raise ValueError(f"Invalid source_id: {sid}.")
        page_count = int(phase03.get("page_count", len(phase03.get("pages", []))))
        total_pages += page_count
        sources.append(
            {
                "source_id": sid,
                "filename": descriptor["filename"],
                "stored_relative_path": descriptor["stored_relative_path"],
                "source_document_id": phase03.get("document_id"),
                "file_sha256": phase03.get("file_sha256") or descriptor.get("sha256"),
                "file_size_bytes": descriptor.get("size_bytes"),
                "page_count": page_count,
                "ocr_used": bool(phase03.get("ocr_used")),
                "extraction_metadata": {
                    "schema_version": phase03.get("schema_version"),
                    "ingested_at": phase03.get("ingested_at"),
                    "ocr_language": phase03.get("ocr_language"),
                    "ocr_dpi": phase03.get("ocr_dpi"),
                    "ocr_psm": phase03.get("ocr_psm"),
                },
                "pages": copy.deepcopy(phase03.get("pages", [])),
            }
        )
    return {
        "schema_version": "ai-document.phase03-combined/v1",
        "assessment_id": assessment_id,
        "document_id": assessment_id,
        "created_at": now_utc(),
        "source_count": len(sources),
        "page_count": total_pages,
        "ocr_used": any(item["ocr_used"] for item in sources),
        "sources": sources,
    }


def combine_phase04_sources(
    assessment_id: str,
    source_results: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    if not ASSESSMENT_ID_RE.fullmatch(assessment_id):
        raise ValueError("Invalid assessment_id.")
    chunks: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    embedding_specs: set[tuple[str, str, int]] = set()
    embedded_count = 0
    for descriptor, phase04 in source_results:
        sid = str(descriptor["source_id"])
        if not SOURCE_ID_RE.fullmatch(sid):
            raise ValueError(f"Invalid source_id: {sid}.")
        sources.append(
            {
                "source_id": sid,
                "filename": descriptor["filename"],
                "stored_relative_path": descriptor["stored_relative_path"],
                "source_document_id": descriptor.get("source_document_id"),
            }
        )
        for source_index, original in enumerate(phase04.get("chunks", []), start=1):
            chunk = copy.deepcopy(original)
            chunk_id = f"{assessment_id}_{sid}_C{source_index:04d}"
            chunk["chunk_id"] = chunk_id
            chunk["document_id"] = assessment_id
            chunk["assessment_id"] = assessment_id
            chunk["source_id"] = sid
            chunk["source_filename"] = descriptor["filename"]
            chunk.setdefault("metadata", {})
            chunk["metadata"].update(
                {
                    "source_id": sid,
                    "source_filename": descriptor["filename"],
                    "source_document_id": descriptor.get("source_document_id"),
                }
            )
            if isinstance(chunk.get("llm_ready"), dict):
                chunk["llm_ready"]["evidence_id"] = chunk_id
                citation = chunk["llm_ready"].setdefault("citation", {})
                citation.update(
                    {
                        "source_id": sid,
                        "source_filename": descriptor["filename"],
                    }
                )
            embedding = chunk.get("embedding") or {}
            vector = embedding.get("vector")
            if isinstance(vector, list) and vector:
                embedded_count += 1
                embedding_specs.add(
                    (str(embedding.get("provider", "")), str(embedding.get("model", "")), len(vector))
                )
            chunks.append(chunk)
    if embedded_count not in {0, len(chunks)}:
        raise ValueError("Combined Phase 04 contains a partial set of embeddings.")
    if len(embedding_specs) > 1:
        raise ValueError("Combined Phase 04 contains incompatible embedding metadata.")
    if embedding_specs and any(not provider or not model for provider, model, _ in embedding_specs):
        raise ValueError("Combined Phase 04 embedding metadata is incomplete.")
    chunk_ids = [str(chunk.get("chunk_id")) for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
        raise ValueError("Combined Phase 04 contains duplicate chunk IDs.")
    return {
        "schema_version": "ai-document.phase04-combined/v1",
        "assessment_id": assessment_id,
        "document_id": assessment_id,
        "created_at": now_utc(),
        "metadata": {
            "phase": "04-document-understanding-combined",
            "source_count": len(sources),
            "embedding_specification": (
                {
                    "provider": next(iter(embedding_specs))[0],
                    "model": next(iter(embedding_specs))[1],
                    "dimensions": next(iter(embedding_specs))[2],
                }
                if embedding_specs
                else None
            ),
        },
        "sources": sources,
        "chunks": chunks,
    }


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
