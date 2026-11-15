from __future__ import annotations

import json
import os
import re
import hashlib
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.ingest import ingest_pdf
from phase04.config import Settings
from phase04.pipeline import build_phase04
from phase06.summary import build_summary
from phase06.validation import validate_summary_integrity
from phase07.report import build_report
from phase07.scoring import calculate_scoring
from webapp.file_manager import FileManager, FileManagerError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "data" / "input" / "uploads"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
SOURCE_INDEX_PATH = INPUT_DIR / ".file-manager-metadata.json"
RUBRIC_PATH = PROJECT_ROOT.parent / "data" / "knowledge_base" / "iop_reference.json"
GOOD_EXAMPLES_PATH = PROJECT_ROOT.parent / "data" / "knowledge_base" / "good_examples_kb.json"
LLM_PROVIDERS = {"none", "anthropic", "huggingface", "ollama", "openai"}
REPORT_FORMATS = {"pdf", "docx", "both"}
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "huggingface": "Qwen/Qwen3-8B",
    "openai": "gpt-4.1-mini",
}
IOP_DIMENSIONS = tuple(f"D{index}" for index in range(1, 9))
# Keep the Qwen request context small enough for the local 8 GB RTX 4060.
WEB_LLM_TOP_K = 2
WEB_SEMANTIC_WEIGHT = 0.7
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = Lock()
SOURCE_INDEX_LOCK = Lock()


def _configured_output_roots() -> list[Path]:
    configured = os.getenv("IOP_FILE_MANAGER_OUTPUT_ROOTS", "").strip()
    if not configured:
        return [OUTPUT_DIR]
    roots: list[Path] = []
    for value in configured.split(os.pathsep):
        if not value.strip():
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise RuntimeError("IOP_FILE_MANAGER_OUTPUT_ROOTS must contain absolute paths")
        resolved = candidate.resolve()
        protected = [PROJECT_ROOT.resolve(), INPUT_DIR.resolve(), RUBRIC_PATH.resolve(), GOOD_EXAMPLES_PATH.resolve()]
        if any(
            resolved == item or resolved in item.parents or item in resolved.parents
            for item in protected
        ):
            raise RuntimeError(f"Unsafe managed output root: {resolved}")
        roots.append(resolved)
    if not roots:
        raise RuntimeError("IOP_FILE_MANAGER_OUTPUT_ROOTS did not contain a valid path")
    return roots


def _file_has_active_job(document_id: str | None, path: Path) -> bool:
    candidate = path.resolve()
    with JOBS_LOCK:
        for job in JOBS.values():
            if job.get("status") not in {"queued", "running"}:
                continue
            if document_id and job.get("document_id") == document_id:
                return True
            for raw_path in job.get("paths", {}).values():
                try:
                    if raw_path and Path(raw_path).resolve() == candidate:
                        return True
                except (OSError, TypeError, ValueError):
                    continue
    return False


def _source_index() -> dict[str, str]:
    with SOURCE_INDEX_LOCK:
        try:
            value = json.loads(SOURCE_INDEX_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return {}
    return {
        stored: original
        for stored, original in value.items()
        if isinstance(stored, str) and Path(stored).name == stored
        and isinstance(original, str) and Path(original).name == original
    } if isinstance(value, dict) else {}


def _source_original_name(path: Path) -> str | None:
    return _source_index().get(path.name)


def _record_source_name(stored_name: str, original_name: str) -> None:
    with SOURCE_INDEX_LOCK:
        try:
            current = json.loads(SOURCE_INDEX_PATH.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                current = {}
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            current = {}
        current[stored_name] = original_name
        temporary = SOURCE_INDEX_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(SOURCE_INDEX_PATH)


_id_secret_text = os.getenv("IOP_FILE_MANAGER_ID_SECRET", "").strip()
_id_secret = (
    _id_secret_text.encode("utf-8")
    if _id_secret_text
    else hashlib.sha256(f"ai-document-file-manager:{PROJECT_ROOT.resolve()}".encode()).digest()
)
FILE_MANAGER = FileManager(
    _configured_output_roots(),
    INPUT_DIR,
    access_token=os.getenv("IOP_FILE_MANAGER_ACCESS_TOKEN"),
    active_document=_file_has_active_job,
    source_name_for_path=_source_original_name,
    id_secret=_id_secret,
)

app = FastAPI(title="IOP AI Document Assessment", version="1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_job(job_id: str, **changes: Any) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(changes, updated_at=_now())


def _job_payload(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="ไม่พบงานที่ระบุ")
        return {key: value for key, value in job.items() if key != "paths"}


def _run_pipeline(
    job_id: str,
    source_path: Path,
    provider: str,
    model: str | None,
    dimensions: list[str],
    report_format: str,
) -> None:
    try:
        _update_job(job_id, status="running", stage="phase03", message="กำลังอ่านข้อความจาก PDF และ OCR เมื่อจำเป็น", progress=20)
        phase03_path, phase03 = ingest_pdf(str(source_path), str(OUTPUT_DIR))
        _update_job(
            job_id,
            document_id=phase03["document_id"],
            page_count=phase03["page_count"],
            ocr_used=phase03["ocr_used"],
            paths={"source": str(source_path), "phase03": str(phase03_path)},
        )

        phase04_settings = Settings.load()
        create_embeddings = phase04_settings.embedding_provider != "none"
        phase04_message = "กำลังแบ่งข้อความเป็น evidence chunks"
        if create_embeddings:
            phase04_message += " และสร้าง embeddings"
        _update_job(job_id, stage="phase04", message=phase04_message, progress=45)
        phase04 = build_phase04(
            phase03,
            phase04_settings,
            embed=create_embeddings,
        )
        phase04_path = OUTPUT_DIR / f"{phase03['document_id']}_phase04.json"
        phase04_path.write_text(json.dumps(phase04, ensure_ascii=False, indent=2), encoding="utf-8")

        phase05_path: Path | None = None
        if provider != "none":
            provider_name = {
                "huggingface": "Qwen3-8B ในเครื่อง",
                "ollama": "Ollama",
                "openai": "OpenAI API",
                "anthropic": "Claude API",
            }[provider]
            _update_job(job_id, stage="phase05", message=f"{provider_name} กำลังประเมินตามเกณฑ์ IOP", progress=65)
            dimension_suffix = (
                ""
                if dimensions == list(IOP_DIMENSIONS)
                else "_" + "-".join(dimensions)
            )
            phase05_path = OUTPUT_DIR / f"{phase03['document_id']}_phase05{dimension_suffix}_assessment.json"
            command = [
                sys.executable, "-m", "phase05.cli",
                "--phase04", str(phase04_path),
                "--rubric", str(RUBRIC_PATH),
                "--output", str(phase05_path),
                "--provider", provider,
                "--top-k", str(WEB_LLM_TOP_K),
                "--retrieval", "hybrid",
                "--semantic-weight", str(WEB_SEMANTIC_WEIGHT),
                "--dimensions", ",".join(dimensions),
            ]
            if model:
                command.extend(["--model", model])
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTORCH_CUDA_ALLOC_CONF": os.getenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")},
            )
            phase05_log = []
            assert process.stdout is not None
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                phase05_log.append(line)
                match = re.match(r"^\[(\d+)/(\d+)\]\s+(.+)$", line)
                if match:
                    dimension_index = int(match.group(1))
                    total_dimensions = int(match.group(2))
                    detail = match.group(3)
                    normalized_detail = detail.lower()
                    completed_dimensions = (
                        dimension_index
                        if normalized_detail.startswith(("completed ", "skipped "))
                        else dimension_index - 1
                    )
                    progress = 65 + round(
                        29 * completed_dimensions / total_dimensions
                    )
                    _update_job(
                        job_id,
                        message=f"{provider_name}: {detail}",
                        progress=progress,
                    )
            return_code = process.wait()
            if return_code != 0:
                detail = "\n".join(phase05_log[-50:])
                raise RuntimeError(
                    detail or f"Phase 05 exited with status {return_code}."
                )

            _update_job(
                job_id,
                stage="phase06",
                message="กำลังจัดทำสรุปผลสำหรับรายงาน",
                progress=95,
            )
            good_examples = json.loads(GOOD_EXAMPLES_PATH.read_text(encoding="utf-8"))
            phase05 = json.loads(phase05_path.read_text(encoding="utf-8"))
            phase06 = build_summary(phase05, good_examples, "none", None)
            validate_summary_integrity(phase06)
            phase06_path = OUTPUT_DIR / f"{phase03['document_id']}_phase06_summary.json"
            phase06_path.write_text(
                json.dumps(phase06, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            _update_job(
                job_id,
                stage="phase07",
                message="กำลังสร้างไฟล์รายงาน",
                progress=98,
            )
            report_paths: dict[str, Path] = {}
            rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
            scoring = calculate_scoring(phase06, rubric)
            scoring_path = OUTPUT_DIR / f"{phase03['document_id']}_phase07_scoring.json"
            scoring_path.write_text(
                json.dumps(
                    {
                        "schema_version": "ai-document.phase07-scoring/v1",
                        "document_id": phase03["document_id"],
                        "scoring": scoring,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            extensions = ("pdf", "docx") if report_format == "both" else (report_format,)
            for extension in extensions:
                report_path = OUTPUT_DIR / f"{phase03['document_id']}_iop_report.{extension}"
                build_report(phase06, report_path, rubric)
                report_paths[f"report_{extension}"] = report_path

        paths = {"source": str(source_path), "phase03": str(phase03_path), "phase04": str(phase04_path)}
        if phase05_path:
            paths["phase05"] = str(phase05_path)
            paths["phase06"] = str(phase06_path)
            paths["phase07_scoring"] = str(scoring_path)
            paths.update({name: str(path) for name, path in report_paths.items()})
        _update_job(
            job_id,
            status="completed",
            stage="completed",
            message="ประมวลผลเสร็จแล้ว",
            progress=100,
            document_id=phase03["document_id"],
            page_count=phase03["page_count"],
            ocr_used=phase03["ocr_used"],
            paths=paths,
        )
    except subprocess.CalledProcessError as exc:
        # Surface the subprocess traceback in the local UI. Previously only
        # the generic exit status was shown, which hid Hugging Face/CUDA errors.
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        _update_job(job_id, status="failed", stage="failed", message=detail, progress=100)
    except Exception as exc:
        _update_job(job_id, status="failed", stage="failed", message=str(exc), progress=100)


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    page = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(page)


@app.post("/api/jobs", status_code=202)
async def create_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    provider: str = Form("huggingface"),
    model: str | None = Form(None),
    dimensions: list[str] | None = Form(None),
    report_format: str = Form("pdf"),
) -> dict[str, str]:
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="รองรับเฉพาะไฟล์ PDF")
    provider = provider.strip().lower()
    if provider not in LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail="ไม่รองรับ LLM provider นี้")
    report_format = report_format.strip().lower()
    if report_format not in REPORT_FORMATS:
        raise HTTPException(status_code=400, detail="ไม่รองรับรูปแบบรายงานนี้")
    model = model.strip() if model else None
    if provider == "ollama" and not model:
        raise HTTPException(status_code=400, detail="กรุณาระบุ Ollama model tag จาก /api/tags")
    if provider != "none" and not model:
        model = DEFAULT_MODELS[provider]
    selected_dimensions = []
    for dimension_id in dimensions or []:
        normalized = dimension_id.strip().upper()
        if normalized in IOP_DIMENSIONS and normalized not in selected_dimensions:
            selected_dimensions.append(normalized)
    selected_dimensions.sort(key=IOP_DIMENSIONS.index)
    if provider != "none" and not selected_dimensions:
        raise HTTPException(status_code=400, detail="กรุณาเลือกอย่างน้อยหนึ่งมิติ")
    if not selected_dimensions:
        selected_dimensions = list(IOP_DIMENSIONS)
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    source_path = INPUT_DIR / f"{job_id}.pdf"
    with source_path.open("wb") as target:
        shutil.copyfileobj(file.file, target)
    await file.close()
    _record_source_name(source_path.name, Path(file.filename).name)

    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "source_name": Path(file.filename).name,
            "provider": provider,
            "model": model,
            "dimensions": selected_dimensions,
            "report_format": report_format,
            "status": "queued",
            "stage": "queued",
            "message": "รอเริ่มประมวลผล",
            "progress": 0,
            "created_at": _now(),
            "updated_at": _now(),
            "paths": {"source": str(source_path)},
        }
    background_tasks.add_task(
        _run_pipeline,
        job_id,
        source_path,
        provider,
        model,
        selected_dimensions,
        report_format,
    )
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _job_payload(job_id)


@app.get("/api/jobs/{job_id}/output/{output_name}")
def download_output(job_id: str, output_name: str) -> FileResponse:
    if output_name not in {"phase03", "phase04", "phase05", "phase06", "phase07_scoring", "report_pdf", "report_docx"}:
        raise HTTPException(status_code=404, detail="ไม่พบชนิดผลลัพธ์")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        path = Path(job["paths"].get(output_name, "")) if job else None
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="ผลลัพธ์ยังไม่พร้อม")
    media_types = {
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return FileResponse(path, media_type=media_types.get(path.suffix.lower()), filename=path.name)


def _file_manager_http_error(exc: FileManagerError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})


@app.get("/api/files")
def list_managed_files() -> dict[str, Any]:
    return FILE_MANAGER.inventory()


@app.post("/api/files/access/check")
def check_file_manager_access(
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    try:
        FILE_MANAGER.authorize(authorization)
    except FileManagerError as exc:
        raise _file_manager_http_error(exc) from exc
    return {"authorized": True}


@app.get("/api/files/{file_id}/download")
def download_managed_file(file_id: str) -> FileResponse:
    try:
        item = FILE_MANAGER.locate(file_id)
    except FileManagerError as exc:
        raise _file_manager_http_error(exc) from exc
    media_types = {
        "json": "application/json",
        "jsonl": "application/x-ndjson",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return FileResponse(item.path, media_type=media_types.get(item.extension), filename=item.filename)


@app.delete("/api/files/{file_id}")
def delete_managed_file(
    file_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return FILE_MANAGER.delete_one(file_id, authorization, confirmed=payload.get("confirmed") is True)
    except FileManagerError as exc:
        raise _file_manager_http_error(exc) from exc


@app.post("/api/documents/{document_id}/outputs/delete/preview")
def preview_document_output_deletion(
    document_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return FILE_MANAGER.bulk_preview(document_id, authorization)
    except FileManagerError as exc:
        raise _file_manager_http_error(exc) from exc


@app.post("/api/documents/{document_id}/outputs/delete")
def delete_document_outputs(
    document_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return FILE_MANAGER.bulk_delete(
            document_id,
            authorization,
            str(payload.get("confirmation_token", "")),
            confirmed=payload.get("confirmed") is True,
        )
    except FileManagerError as exc:
        raise _file_manager_http_error(exc) from exc
