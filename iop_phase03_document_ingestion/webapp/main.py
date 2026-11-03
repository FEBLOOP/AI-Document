from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.ingest import ingest_pdf
from phase04.config import Settings
from phase04.pipeline import build_phase04
from phase06.summary import build_summary
from phase06.validation import validate_summary_integrity
from phase07.report import build_report
from phase07.scoring import calculate_scoring


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "data" / "input" / "uploads"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
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

        paths = {"phase03": str(phase03_path), "phase04": str(phase04_path)}
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
            "paths": {},
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
