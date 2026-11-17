from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from fastapi import BackgroundTasks, HTTPException

from phase05.retrieval import evidence_item, retrieve
from phase06.summary import build_summary
from webapp.file_manager import FileManager
from webapp.multi_assessment import (
    build_combined_phase03,
    combine_phase04_sources,
    sanitize_pdf_filename,
)
import webapp.main as web_main


ASSESSMENT_ID = "ASSESSMENT_AAAAAAAAAAAA"


def _run_immediate(coroutine):
    """Drive upload coroutines whose in-memory UploadFile operations do not yield.

    The sandboxed Windows Python cannot create asyncio's internal socketpair;
    production FastAPI still executes these functions on its normal event loop.
    """
    try:
        coroutine.send(None)
    except StopIteration as stopped:
        return stopped.value
    raise AssertionError("The test coroutine unexpectedly yielded asynchronous I/O.")


def _pdf_bytes(text: str = "innovation policy and governance") -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), (text + " documented evidence ") * 4)
    payload = document.tobytes()
    document.close()
    return payload


class _MemoryUpload:
    def __init__(self, name: str, payload: bytes) -> None:
        self.filename = name
        self.content_type = "application/pdf"
        self.file = io.BytesIO(payload)

    async def read(self, size: int = -1) -> bytes:
        return self.file.read(size)

    async def close(self) -> None:
        self.file.close()


def _upload(name: str, text: str) -> _MemoryUpload:
    return _MemoryUpload(name, _pdf_bytes(text))


def _descriptor(index: int, filename: str) -> dict:
    return {
        "source_id": f"SOURCE_{index:03d}",
        "filename": filename,
        "stored_relative_path": f"{ASSESSMENT_ID}/SOURCE_{index:03d}.pdf",
        "source_document_id": f"DOC_{index:012X}",
        "size_bytes": 100,
        "sha256": f"hash-{index}",
    }


def _phase03(index: int, text: str = "evidence") -> dict:
    return {
        "document_id": f"DOC_{index:012X}",
        "file_sha256": f"hash-{index}",
        "page_count": 1,
        "ocr_used": index % 2 == 0,
        "pages": [{"page": 1, "text": text, "blocks": [{"block_id": "P1_B1", "text": text}]}],
    }


def _phase04(index: int, *, vector: list[float] | None = None) -> dict:
    chunk = {
        "chunk_id": f"DOC_{index:012X}_C0001",
        "document_id": f"DOC_{index:012X}",
        "normalized_text": f"innovation evidence source {index}",
        "page_start": 1,
        "page_end": 1,
        "source_blocks": [{"block_id": "P1_B1", "page": 1}],
        "metadata": {},
        "llm_ready": {"evidence_id": "old", "citation": {}},
    }
    if vector is not None:
        chunk["embedding"] = {"provider": "sentence_transformers", "model": "model", "vector": vector}
    return {"document_id": chunk["document_id"], "chunks": [chunk]}


def _good_examples() -> dict:
    return {
        "knowledge_base": {
            "name": "Examples",
            "version": "1",
            "rules": {"affects_score": False, "affects_classification": False, "used_for_summary_style": True},
            "overall_example": {"structure": []},
            "dimensions": [{"dimension_id": "D1", "name_th": "D1", "summary_style": {"structure": []}, "criteria": []}],
        }
    }


def test_filename_validation_rejects_unsafe_paths() -> None:
    assert sanitize_pdf_filename("report.pdf") == "report.pdf"
    for unsafe in ("../report.pdf", "folder/report.pdf", "folder\\report.pdf", "report.txt", ""):
        with pytest.raises(ValueError):
            sanitize_pdf_filename(unsafe)


def test_combined_phase03_preserves_duplicate_filenames_pages_and_ocr() -> None:
    combined = build_combined_phase03(
        ASSESSMENT_ID,
        [(_descriptor(1, "same.pdf"), _phase03(1)), (_descriptor(2, "same.pdf"), _phase03(2))],
    )
    assert combined["source_count"] == 2
    assert [source["source_id"] for source in combined["sources"]] == ["SOURCE_001", "SOURCE_002"]
    assert [source["filename"] for source in combined["sources"]] == ["same.pdf", "same.pdf"]
    assert all(source["pages"][0]["page"] == 1 for source in combined["sources"])
    assert combined["ocr_used"] is True


def test_combined_phase04_has_unique_ids_source_attribution_and_total_top_k() -> None:
    combined = combine_phase04_sources(
        ASSESSMENT_ID,
        [(_descriptor(index, f"source-{index}.pdf"), _phase04(index)) for index in range(1, 4)],
    )
    ids = [chunk["chunk_id"] for chunk in combined["chunks"]]
    assert len(ids) == len(set(ids)) == 3
    assert combined["chunks"][1]["source_filename"] == "source-2.pdf"
    assert combined["chunks"][1]["page_start"] == 1
    hits = retrieve(combined["chunks"], "innovation evidence", 2, mode="lexical")
    assert len(hits) == 2
    assert len({hit["chunk"]["source_id"] for hit in hits}) == 2
    evidence = evidence_item(hits[0])
    assert evidence["source_filename"].endswith(".pdf")
    assert evidence["source_id"].startswith("SOURCE_")


def test_combined_phase04_rejects_partial_or_incompatible_embeddings() -> None:
    with pytest.raises(ValueError, match="partial"):
        combine_phase04_sources(
            ASSESSMENT_ID,
            [(_descriptor(1, "a.pdf"), _phase04(1, vector=[1.0, 0.0])), (_descriptor(2, "b.pdf"), _phase04(2))],
        )
    with pytest.raises(ValueError, match="incompatible"):
        combine_phase04_sources(
            ASSESSMENT_ID,
            [(_descriptor(1, "a.pdf"), _phase04(1, vector=[1.0, 0.0])), (_descriptor(2, "b.pdf"), _phase04(2, vector=[1.0, 0.0, 0.0]))],
        )


def test_phase06_preserves_sources_scores_citations_and_mapping() -> None:
    source = {
        "schema_version": "ai-document.phase05/v1",
        "assessment_id": ASSESSMENT_ID,
        "document_id": ASSESSMENT_ID,
        "sources": [{"source_id": "SOURCE_001", "filename": "a.pdf"}],
        "dimensions": [{
            "dimension_id": "D1",
            "criteria": [{
                "criterion": {"criterion_id": "1.1.1", "component_id": "1.1", "criterion": "criterion", "dimension_name": "D1"},
                "evidence": [{"chunk_id": "C1", "source_id": "SOURCE_001", "source_filename": "a.pdf", "page_start": 1, "page_end": 1, "text": "evidence"}],
                "assessment": {"score": 3, "confidence": 0.8, "evidence_chunk_ids": ["C1"], "strengths": ["strength"], "strength_evidence_mapping": [{"strength": "strength", "evidence_chunk_ids": ["C1"]}], "missing_points": []},
                "status": "assessed",
            }],
        }],
    }
    summary = build_summary(source, _good_examples())
    criterion = summary["dimension_summaries"][0]["criterion_summaries"][0]
    assert summary["sources"] == source["sources"]
    assert summary["source_assessment"]["sources"] == source["sources"]
    assert criterion["score"] == 3
    assert criterion["citations"][0]["source_filename"] == "a.pdf"
    assert criterion["strength_evidence_mapping"][0]["evidence_chunk_ids"] == ["C1"]


def test_single_upload_route_is_preserved(tmp_path: Path, monkeypatch) -> None:
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(web_main, "INPUT_DIR", upload_root)
    monkeypatch.setattr(web_main, "SOURCE_INDEX_PATH", upload_root / ".file-manager-metadata.json")
    with web_main.JOBS_LOCK:
        web_main.JOBS.clear()
    background = BackgroundTasks()
    result = _run_immediate(web_main.create_job(background, _upload("one.pdf", "single"), "none", None, None, "pdf"))
    assert result["id"] in web_main.JOBS
    assert len(background.tasks) == 1
    assert next(upload_root.glob("*.pdf")).is_file()


def test_three_pdf_submission_produces_one_combined_assessment(tmp_path: Path, monkeypatch) -> None:
    upload_root = tmp_path / "uploads"
    output_root = tmp_path / "output"
    work_root = tmp_path / "work"
    monkeypatch.setattr(web_main, "INPUT_DIR", upload_root)
    monkeypatch.setattr(web_main, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(web_main, "WORK_DIR", work_root)
    monkeypatch.setattr(web_main, "SOURCE_INDEX_PATH", upload_root / ".file-manager-metadata.json")
    settings = SimpleNamespace(
        embedding_provider="hash",
        embedding_model="hash",
        embedding_dimensions=8,
        chunk_max_chars=2000,
        chunk_overlap_blocks=0,
        min_section_heading_chars=80,
    )
    monkeypatch.setattr(web_main, "Settings", SimpleNamespace(load=lambda: settings))
    with web_main.JOBS_LOCK:
        web_main.JOBS.clear()
    background = BackgroundTasks()
    result = _run_immediate(web_main.create_multi_assessment(
        background,
        [_upload("duplicate.pdf", "innovation one"), _upload("duplicate.pdf", "innovation two"), _upload("third.pdf", "innovation three")],
        "none", None, None, "pdf",
    ))
    assert result["assessment_id"].startswith("ASSESSMENT_")
    task = background.tasks[0]
    task.func(*task.args, **task.kwargs)
    job = web_main.JOBS[result["id"]]
    assert job["status"] == "completed"
    assert job["source_count"] == 3
    assert job["document_id"] == result["assessment_id"]
    combined = json.loads(Path(job["paths"]["phase04"]).read_text(encoding="utf-8"))
    assert len(combined["sources"]) == 3
    assert len({chunk["chunk_id"] for chunk in combined["chunks"]}) == len(combined["chunks"])
    manifest = json.loads(Path(job["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["outputs"]["phase04"] == Path(job["paths"]["phase04"]).name
    assert not (work_root / result["assessment_id"]).exists()


def test_multi_upload_rejects_corrupt_pdf(tmp_path: Path, monkeypatch) -> None:
    upload_root = tmp_path / "uploads"
    monkeypatch.setattr(web_main, "INPUT_DIR", upload_root)
    monkeypatch.setattr(web_main, "SOURCE_INDEX_PATH", upload_root / ".file-manager-metadata.json")
    corrupt = _MemoryUpload("bad.pdf", b"not a pdf")
    with pytest.raises(HTTPException, match="เสียหาย"):
        _run_immediate(web_main.create_multi_assessment(BackgroundTasks(), [corrupt, _upload("good.pdf", "good")], "none", None, None, "pdf"))
    assert not list(upload_root.glob("ASSESSMENT_*"))


def test_multi_upload_rejects_oversized_and_concurrent_duplicate(tmp_path: Path, monkeypatch) -> None:
    upload_root = tmp_path / "uploads"
    output_root = tmp_path / "output"
    monkeypatch.setattr(web_main, "INPUT_DIR", upload_root)
    monkeypatch.setattr(web_main, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(web_main, "SOURCE_INDEX_PATH", upload_root / ".file-manager-metadata.json")
    with web_main.JOBS_LOCK:
        web_main.JOBS.clear()

    monkeypatch.setattr(web_main, "MULTI_MAX_FILE_BYTES", 5)
    with pytest.raises(HTTPException) as oversized:
        _run_immediate(web_main.create_multi_assessment(
            BackgroundTasks(), [_upload("large.pdf", "large"), _upload("other.pdf", "other")],
            "none", None, None, "pdf",
        ))
    assert oversized.value.status_code == 413

    monkeypatch.setattr(web_main, "MULTI_MAX_FILE_BYTES", 50 * 1024 * 1024)
    payload_a = _pdf_bytes("same-a")
    payload_b = _pdf_bytes("same-b")
    first = _run_immediate(web_main.create_multi_assessment(
        BackgroundTasks(), [_MemoryUpload("a.pdf", payload_a), _MemoryUpload("b.pdf", payload_b)],
        "none", None, None, "pdf",
    ))
    with pytest.raises(HTTPException) as duplicate:
        _run_immediate(web_main.create_multi_assessment(
            BackgroundTasks(), [_MemoryUpload("a.pdf", payload_a), _MemoryUpload("b.pdf", payload_b)],
            "none", None, None, "pdf",
        ))
    assert duplicate.value.status_code == 409
    assert web_main.JOBS[first["id"]]["status"] == "queued"


def test_required_source_failure_marks_assessment_failed_and_stops(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "output"
    work_root = tmp_path / "work"
    upload_root = tmp_path / "uploads" / ASSESSMENT_ID
    upload_root.mkdir(parents=True)
    sources = []
    for index, filename in enumerate(("good.pdf", "broken.pdf"), start=1):
        path = upload_root / f"SOURCE_{index:03d}.pdf"
        path.write_bytes(_pdf_bytes(filename))
        descriptor = _descriptor(index, filename)
        descriptor["path"] = path
        sources.append(descriptor)
    monkeypatch.setattr(web_main, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(web_main, "WORK_DIR", work_root)
    write_json = {
        "schema_version": "ai-document.assessment-manifest/v1",
        "assessment_id": ASSESSMENT_ID,
        "document_id": ASSESSMENT_ID,
        "status": "queued",
    }
    output_root.mkdir()
    (output_root / f"{ASSESSMENT_ID}_manifest.json").write_text(json.dumps(write_json), encoding="utf-8")

    calls = []
    def fake_ingest(path: str, output: str):
        calls.append(path)
        if path.endswith("SOURCE_002.pdf"):
            raise ValueError("cannot extract")
        return Path(output) / "source.json", _phase03(1)

    monkeypatch.setattr(web_main, "ingest_pdf", fake_ingest)
    job_id = "failed-job"
    with web_main.JOBS_LOCK:
        web_main.JOBS.clear()
        web_main.JOBS[job_id] = {
            "id": job_id, "document_id": ASSESSMENT_ID, "status": "queued",
            "progress": 0, "paths": {}, "created_at": web_main._now(), "updated_at": web_main._now(),
        }
    web_main._run_multi_pipeline(job_id, ASSESSMENT_ID, sources, "none", None, list(web_main.IOP_DIMENSIONS), "pdf")
    assert len(calls) == 2
    assert web_main.JOBS[job_id]["status"] == "failed"
    assert "broken.pdf" in web_main.JOBS[job_id]["message"]
    assert not (output_root / f"{ASSESSMENT_ID}_phase04.json").exists()
    manifest = json.loads((output_root / f"{ASSESSMENT_ID}_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert not (work_root / ASSESSMENT_ID).exists()


def test_startup_recovery_cleans_only_orphaned_assessment_work(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "output"
    work_root = tmp_path / "work"
    orphan = work_root / ASSESSMENT_ID
    unrelated = work_root / "do-not-touch"
    orphan.mkdir(parents=True)
    unrelated.mkdir()
    (orphan / "owner.json").write_text(json.dumps({"pid": 999999, "job_id": "old"}), encoding="utf-8")
    output_root.mkdir()
    (output_root / f"{ASSESSMENT_ID}_manifest.json").write_text(json.dumps({
        "assessment_id": ASSESSMENT_ID,
        "document_id": ASSESSMENT_ID,
        "status": "running",
        "stage": "phase04",
    }), encoding="utf-8")
    monkeypatch.setattr(web_main, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(web_main, "WORK_DIR", work_root)
    monkeypatch.setattr(web_main, "_process_is_alive", lambda pid: False)

    web_main._recover_interrupted_multi_jobs()

    assert not orphan.exists()
    assert unrelated.exists()
    manifest = json.loads((output_root / f"{ASSESSMENT_ID}_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["stage"] == "interrupted"


def test_file_manager_groups_manifest_sources_under_assessment(tmp_path: Path) -> None:
    output = tmp_path / "output"
    source = tmp_path / "uploads"
    stored = source / ASSESSMENT_ID
    output.mkdir()
    stored.mkdir(parents=True)
    for index in (1, 2):
        (stored / f"SOURCE_{index:03d}.pdf").write_bytes(_pdf_bytes(str(index)))
    (output / f"{ASSESSMENT_ID}_manifest.json").write_text(json.dumps({
        "assessment_id": ASSESSMENT_ID,
        "document_id": ASSESSMENT_ID,
        "sources": [
            {"source_id": f"SOURCE_{index:03d}", "filename": f"source-{index}.pdf", "stored_relative_path": f"{ASSESSMENT_ID}/SOURCE_{index:03d}.pdf"}
            for index in (1, 2)
        ],
    }), encoding="utf-8")
    (output / f"{ASSESSMENT_ID}_phase04.json").write_text(json.dumps({"assessment_id": ASSESSMENT_ID, "document_id": ASSESSMENT_ID}), encoding="utf-8")
    names = {f"SOURCE_{index:03d}.pdf": f"source-{index}.pdf" for index in (1, 2)}
    manager = FileManager(
        [output], source, id_secret=b"multi-test",
        source_name_for_path=lambda path: names.get(path.name),
    )
    records = manager.inventory()["files"]
    assert len(records) == 4
    assert {record["document_id"] for record in records} == {ASSESSMENT_ID}
    assert {record["original_filename"] for record in records if record["is_source"]} == {"source-1.pdf", "source-2.pdf"}
