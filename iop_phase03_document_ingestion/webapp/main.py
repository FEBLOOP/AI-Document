from __future__ import annotations

import json
import os
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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "data" / "input" / "uploads"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
RUBRIC_PATH = PROJECT_ROOT.parent / "data" / "knowledge_base" / "iop_reference.json"
LLM_PROVIDERS = {"none", "huggingface", "openai"}
DEFAULT_MODELS = {"huggingface": "Qwen/Qwen3-8B", "openai": "gpt-4.1-mini"}
# Keep the Qwen request context small enough for the local 8 GB RTX 4060.
WEB_LLM_TOP_K = 2
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


def _run_pipeline(job_id: str, source_path: Path, provider: str, model: str | None) -> None:
    try:
        _update_job(job_id, status="running", stage="phase03", message="กำลังอ่านข้อความจาก PDF และ OCR เมื่อจำเป็น", progress=20)
        phase03_path, phase03 = ingest_pdf(str(source_path), str(OUTPUT_DIR))

        _update_job(job_id, stage="phase04", message="กำลังแบ่งข้อความเป็น evidence chunks", progress=45)
        phase04 = build_phase04(phase03, Settings(), embed=False)
        phase04_path = OUTPUT_DIR / f"{phase03['document_id']}_phase04.json"
        phase04_path.write_text(json.dumps(phase04, ensure_ascii=False, indent=2), encoding="utf-8")

        phase05_path: Path | None = None
        if provider != "none":
            provider_name = "Qwen3-8B" if provider == "huggingface" else "OpenAI API"
            _update_job(job_id, stage="phase05", message=f"{provider_name} กำลังประเมินตามเกณฑ์ IOP", progress=65)
            phase05_path = OUTPUT_DIR / f"{phase03['document_id']}_phase05_assessment.json"
            command = [
                sys.executable, "-m", "phase05.cli",
                "--phase04", str(phase04_path),
                "--rubric", str(RUBRIC_PATH),
                "--output", str(phase05_path),
                "--provider", provider,
                "--top-k", str(WEB_LLM_TOP_K),
            ]
            if model:
                command.extend(["--model", model])
            subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTORCH_CUDA_ALLOC_CONF": os.getenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")},
            )

        paths = {"phase03": str(phase03_path), "phase04": str(phase04_path)}
        if phase05_path:
            paths["phase05"] = str(phase05_path)
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
) -> dict[str, str]:
    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="รองรับเฉพาะไฟล์ PDF")
    provider = provider.strip().lower()
    if provider not in LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail="ไม่รองรับ LLM provider นี้")
    model = model.strip() if model else None
    if provider != "none" and not model:
        model = DEFAULT_MODELS[provider]
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
            "status": "queued",
            "stage": "queued",
            "message": "รอเริ่มประมวลผล",
            "progress": 0,
            "created_at": _now(),
            "updated_at": _now(),
            "paths": {},
        }
    background_tasks.add_task(_run_pipeline, job_id, source_path, provider, model)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _job_payload(job_id)


@app.get("/api/jobs/{job_id}/output/{output_name}")
def download_output(job_id: str, output_name: str) -> FileResponse:
    if output_name not in {"phase03", "phase04", "phase05"}:
        raise HTTPException(status_code=404, detail="ไม่พบชนิดผลลัพธ์")
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        path = Path(job["paths"].get(output_name, "")) if job else None
    if not path or not path.is_file():
        raise HTTPException(status_code=404, detail="ผลลัพธ์ยังไม่พร้อม")
    return FileResponse(path, media_type="application/json", filename=path.name)
