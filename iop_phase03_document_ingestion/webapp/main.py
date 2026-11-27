from __future__ import annotations

import json
import os
import re
import hashlib
import shutil
import subprocess
import sys
import uuid
import pymupdf
from dataclasses import replace
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
from webapp.multi_assessment import (
    ASSESSMENT_ID_RE,
    build_combined_phase03,
    combine_phase04_sources,
    new_assessment_id,
    now_utc,
    sanitize_pdf_filename,
    source_id,
    submission_fingerprint,
    write_json_atomic,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "data" / "input" / "uploads"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
WORK_DIR = PROJECT_ROOT / "data" / "work"
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
WEB_LLM_TOP_K = int(os.getenv("IOP_WEB_TOP_K", "5"))
WEB_LLM_BATCH_SIZE = int(os.getenv("IOP_WEB_LLM_BATCH_SIZE", "1"))
WEB_MAX_EVIDENCE_CHARS = int(os.getenv("IOP_WEB_MAX_EVIDENCE_CHARS", "1200"))
WEB_SEMANTIC_WEIGHT = float(os.getenv("IOP_WEB_SEMANTIC_WEIGHT", "0.7"))
WEB_RETRIEVAL_MODE = os.getenv("IOP_WEB_RETRIEVAL_MODE", "hybrid").strip().lower()
WEB_RETRIEVAL_FALLBACK = os.getenv("IOP_WEB_RETRIEVAL_FALLBACK", "error").strip().lower()
WEB_PHASE05_RESTARTS = int(os.getenv("IOP_WEB_PHASE05_RESTARTS", "1"))
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = Lock()
SOURCE_INDEX_LOCK = Lock()
MULTI_MAX_FILES = int(os.getenv("IOP_MULTI_MAX_FILES", "10"))
MULTI_MAX_FILE_BYTES = int(os.getenv("IOP_MULTI_MAX_FILE_BYTES", str(50 * 1024 * 1024)))
MULTI_MAX_TOTAL_BYTES = int(os.getenv("IOP_MULTI_MAX_TOTAL_BYTES", str(200 * 1024 * 1024)))
MULTI_MIN_FREE_BYTES = int(os.getenv("IOP_MULTI_MIN_FREE_BYTES", str(100 * 1024 * 1024)))

if WEB_RETRIEVAL_MODE not in {"lexical", "embedding", "hybrid"}:
    raise RuntimeError("IOP_WEB_RETRIEVAL_MODE must be lexical, embedding, or hybrid")
if WEB_RETRIEVAL_FALLBACK not in {"lexical", "error"}:
    raise RuntimeError("IOP_WEB_RETRIEVAL_FALLBACK must be lexical or error")
if WEB_LLM_TOP_K < 1 or WEB_LLM_BATCH_SIZE < 1 or WEB_MAX_EVIDENCE_CHARS < 1:
    raise RuntimeError("Web Phase 05 top-k, batch size, and evidence characters must be positive")
if not 0 <= WEB_SEMANTIC_WEIGHT <= 1 or WEB_PHASE05_RESTARTS < 0:
    raise RuntimeError("Invalid Web semantic weight or Phase 05 restart count")


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
        if isinstance(stored, str) and not Path(stored).is_absolute()
        and ".." not in Path(stored).parts and "\\" not in stored
        and isinstance(original, str) and Path(original).name == original
    } if isinstance(value, dict) else {}


def _source_original_name(path: Path) -> str | None:
    try:
        key = path.resolve().relative_to(INPUT_DIR.resolve()).as_posix()
    except ValueError:
        key = path.name
    return _source_index().get(key) or _source_index().get(path.name)


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


def _remove_source_names(stored_prefix: str) -> None:
    with SOURCE_INDEX_LOCK:
        try:
            current = json.loads(SOURCE_INDEX_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(current, dict):
            return
        cleaned = {
            key: value
            for key, value in current.items()
            if not (isinstance(key, str) and key.startswith(stored_prefix + "/"))
        }
        temporary = SOURCE_INDEX_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(SOURCE_INDEX_PATH)


def _remove_source_name(path: Path) -> None:
    try:
        stored_name = path.resolve().relative_to(INPUT_DIR.resolve()).as_posix()
    except (OSError, ValueError):
        return
    with SOURCE_INDEX_LOCK:
        try:
            current = json.loads(SOURCE_INDEX_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(current, dict) or stored_name not in current:
            return
        current.pop(stored_name, None)
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
    source_deleted=_remove_source_name,
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


def _normalize_job_config(
    provider: str,
    model: str | None,
    dimensions: list[str] | None,
    report_format: str,
) -> tuple[str, str | None, list[str], str]:
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
    selected: list[str] = []
    for dimension_id in dimensions or []:
        normalized = dimension_id.strip().upper()
        if normalized in IOP_DIMENSIONS and normalized not in selected:
            selected.append(normalized)
    selected.sort(key=IOP_DIMENSIONS.index)
    if provider != "none" and not selected:
        raise HTTPException(status_code=400, detail="กรุณาเลือกอย่างน้อยหนึ่งมิติ")
    return provider, model, selected or list(IOP_DIMENSIONS), report_format


def _manifest_path(assessment_id: str) -> Path:
    return OUTPUT_DIR / f"{assessment_id}_manifest.json"


def _update_manifest(assessment_id: str, **changes: Any) -> None:
    path = _manifest_path(assessment_id)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        manifest = {
            "schema_version": "ai-document.assessment-manifest/v1",
            "assessment_id": assessment_id,
            "document_id": assessment_id,
            "created_at": now_utc(),
        }
    manifest.update(changes, updated_at=now_utc())
    write_json_atomic(path, manifest)


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _recover_interrupted_multi_jobs() -> None:
    """Clean only orphaned assessment workspaces and fail their manifests."""
    if not WORK_DIR.is_dir():
        return
    for work_path in WORK_DIR.iterdir():
        if work_path.is_symlink() or not work_path.is_dir() or not ASSESSMENT_ID_RE.fullmatch(work_path.name):
            continue
        try:
            owner = json.loads((work_path / "owner.json").read_text(encoding="utf-8"))
            owner_pid = int(owner.get("pid", 0))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            owner_pid = 0
        if _process_is_alive(owner_pid):
            continue
        shutil.rmtree(work_path, ignore_errors=True)
        manifest_path = _manifest_path(work_path.name)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            continue
        if manifest.get("status") in {"queued", "running"}:
            _update_manifest(
                work_path.name,
                status="failed",
                stage="interrupted",
                error="งานถูกขัดจังหวะจากการหยุดหรือรีสตาร์ตเว็บเซิร์ฟเวอร์",
            )


def recover_interrupted_multi_jobs() -> None:
    _recover_interrupted_multi_jobs()


app.router.add_event_handler("startup", recover_interrupted_multi_jobs)


def _web_phase04_settings() -> Settings:
    settings = Settings.load()
    if WEB_RETRIEVAL_MODE not in {"embedding", "hybrid"}:
        return settings
    if settings.embedding_provider != "none":
        return settings
    if "PHASE04_EMBEDDING_PROVIDER" in os.environ:
        if WEB_RETRIEVAL_FALLBACK == "lexical":
            return settings
        raise RuntimeError(
            f"Web {WEB_RETRIEVAL_MODE} retrieval requires Phase 04 embeddings, but "
            "PHASE04_EMBEDDING_PROVIDER is explicitly set to 'none'. Configure a "
            "supported provider or set IOP_WEB_RETRIEVAL_MODE=lexical."
        )
    return replace(
        settings,
        embedding_provider="sentence_transformers",
        embedding_model=os.getenv(
            "PHASE04_EMBEDDING_MODEL",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        ),
        embedding_dimensions=int(os.getenv("PHASE04_EMBEDDING_DIMENSIONS", "384")),
    )


def _build_phase04_for_web(
    source: dict[str, Any],
    settings: Settings,
    source_label: str,
) -> tuple[dict[str, Any], Settings, str | None]:
    create_embeddings = settings.embedding_provider != "none"
    try:
        return (
            build_phase04(source, settings, embed=create_embeddings),
            settings,
            None,
        )
    except Exception as exc:
        if create_embeddings and WEB_RETRIEVAL_FALLBACK == "lexical":
            fallback_settings = replace(settings, embedding_provider="none")
            phase04 = build_phase04(source, fallback_settings, embed=False)
            return (
                phase04,
                fallback_settings,
                (
                    f"Phase 04 embeddings unavailable for {source_label}: {exc}. "
                    "Continuing with the explicitly configured lexical fallback."
                ),
            )
        raise RuntimeError(
            f"Phase 04 embedding failed for {source_label} "
            f"with provider={settings.embedding_provider}, "
            f"model={settings.embedding_model}: {exc}"
        ) from exc


def _phase05_command_arguments(
    phase04_path: Path,
    phase05_path: Path,
    provider: str,
    model: str | None,
    dimensions: list[str],
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "phase05.cli",
        "--phase04",
        str(phase04_path),
        "--rubric",
        str(RUBRIC_PATH),
        "--output",
        str(phase05_path),
        "--provider",
        provider,
        "--top-k",
        str(WEB_LLM_TOP_K),
        "--retrieval",
        WEB_RETRIEVAL_MODE,
        "--retrieval-fallback",
        WEB_RETRIEVAL_FALLBACK,
        "--semantic-weight",
        str(WEB_SEMANTIC_WEIGHT),
        "--batch-size",
        str(WEB_LLM_BATCH_SIZE),
        "--max-evidence-chars",
        str(WEB_MAX_EVIDENCE_CHARS),
        "--dimensions",
        ",".join(dimensions),
        #"--resume",
    ]
    if model:
        command.extend(["--model", model])
    return command


def _run_phase05_subprocess(
    job_id: str,
    command: list[str],
    phase05_path: Path,
    provider_name: str,
    progress_start: int,
    progress_span: int,
) -> None:
    combined_log: list[str] = []
    for process_attempt in range(1, WEB_PHASE05_RESTARTS + 2):
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={
                **os.environ,
                "PYTORCH_CUDA_ALLOC_CONF": os.getenv(
                    "PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True"
                ),
            },
        )
        attempt_log: list[str] = []
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            attempt_log.append(line)
            retrieval_match = re.match(
                r"^Retrieval:\s+requested=([a-z_]+),\s+effective=([a-z_]+)$",
                line,
            )
            if retrieval_match:
                requested, effective = retrieval_match.groups()
                _update_job(
                    job_id,
                    retrieval_requested=requested,
                    retrieval_effective=effective,
                    message=f"Retrieval: requested={requested}, effective={effective}",
                )
            progress_match = re.match(r"^\[(\d+)/(\d+)\]\s+(.+)$", line)
            if progress_match:
                current = int(progress_match.group(1))
                total = int(progress_match.group(2))
                detail = progress_match.group(3)
                completed = (
                    current
                    if detail.lower().startswith(("completed ", "skipped "))
                    else current - 1
                )
                _update_job(
                    job_id,
                    message=f"{provider_name}: {detail}",
                    progress=progress_start + round(progress_span * completed / total),
                )
        return_code = process.wait()
        combined_log.extend(attempt_log)
        if return_code == 0:
            return
        if process_attempt <= WEB_PHASE05_RESTARTS and phase05_path.is_file():
            _update_job(
                job_id,
                message=(
                    f"Phase 05 หยุดก่อนจบ กำลังเริ่มประเมินใหม่ "
                    f"({process_attempt}/{WEB_PHASE05_RESTARTS})"
                ),
            )
            continue
        detail = "\n".join(combined_log[-50:])
        with JOBS_LOCK:
            if not JOBS[job_id].get("retrieval_effective"):
                JOBS[job_id].update(
                    retrieval_effective="unavailable",
                    updated_at=_now(),
                )
        raise RuntimeError(detail or f"Phase 05 exited with status {return_code}.")


def _run_multi_pipeline(
    job_id: str,
    assessment_id: str,
    sources: list[dict[str, Any]],
    provider: str,
    model: str | None,
    dimensions: list[str],
    report_format: str,
) -> None:
    work_root = WORK_DIR / assessment_id
    paths: dict[str, str] = {
        "manifest": str(_manifest_path(assessment_id)),
        **{item["source_id"].lower(): str(item["path"]) for item in sources},
    }
    try:
        work_root.mkdir(parents=True, exist_ok=False)
        write_json_atomic(work_root / "owner.json", {"pid": os.getpid(), "job_id": job_id})
        _update_manifest(assessment_id, status="running", stage="phase03")
        phase03_results: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for index, descriptor in enumerate(sources, start=1):
            filename = descriptor["filename"]
            progress = 5 + round(25 * (index - 1) / len(sources))
            _update_job(
                job_id,
                status="running",
                stage="phase03",
                message=f"กำลังอ่านข้อความและ OCR ไฟล์ {index}/{len(sources)}: {filename}",
                progress=progress,
            )
            source_work = work_root / descriptor["source_id"]
            try:
                _, extracted = ingest_pdf(str(descriptor["path"]), str(source_work))
            except Exception as exc:
                raise RuntimeError(f"Phase 03 ไม่สำเร็จสำหรับไฟล์ {filename}: {exc}") from exc
            extracted["source_file"] = filename
            descriptor["source_document_id"] = extracted.get("document_id")
            descriptor["page_count"] = extracted.get("page_count")
            descriptor["ocr_used"] = extracted.get("ocr_used")
            phase03_results.append((descriptor, extracted))

        combined03 = build_combined_phase03(assessment_id, phase03_results)
        phase03_path = OUTPUT_DIR / f"{assessment_id}_phase03.json"
        write_json_atomic(phase03_path, combined03)
        paths["phase03"] = str(phase03_path)
        _update_manifest(
            assessment_id,
            stage="phase04",
            sources=[{key: value for key, value in item.items() if key != "path"} for item in sources],
            outputs={"phase03": phase03_path.name},
        )

        settings = _web_phase04_settings()
        create_embeddings = settings.embedding_provider != "none"
        phase04_results: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for index, (descriptor, extracted) in enumerate(phase03_results, start=1):
            progress = 32 + round(18 * (index - 1) / len(sources))
            _update_job(
                job_id,
                stage="phase04",
                message=f"กำลังสร้าง chunks{(' และ embeddings' if create_embeddings else '')} ไฟล์ {index}/{len(sources)}: {descriptor['filename']}",
                progress=progress,
            )
            try:
                built_phase04, settings, fallback_message = _build_phase04_for_web(
                    extracted,
                    settings,
                    descriptor["filename"],
                )
                create_embeddings = settings.embedding_provider != "none"
                if fallback_message:
                    _update_job(
                        job_id,
                        message=fallback_message,
                        retrieval_effective="lexical",
                    )
                    phase04_results = [
                        (
                            prior_descriptor,
                            build_phase04(
                                prior_extracted,
                                settings,
                                embed=False,
                            ),
                        )
                        for prior_descriptor, prior_extracted in phase03_results[:index]
                    ]
                else:
                    phase04_results.append((descriptor, built_phase04))
            except Exception:
                _update_job(job_id, retrieval_effective="unavailable")
                raise
        combined04 = combine_phase04_sources(assessment_id, phase04_results)
        phase04_path = OUTPUT_DIR / f"{assessment_id}_phase04.json"
        write_json_atomic(phase04_path, combined04)
        paths["phase04"] = str(phase04_path)

        phase05_path: Path | None = None
        manifest_outputs = {"phase03": phase03_path.name, "phase04": phase04_path.name}
        if provider != "none":
            provider_name = {
                "huggingface": "Qwen3-8B ในเครื่อง",
                "ollama": "Ollama",
                "openai": "OpenAI API",
                "anthropic": "Claude API",
            }[provider]
            _update_manifest(assessment_id, stage="phase05", outputs=manifest_outputs)
            _update_job(job_id, stage="phase05", message=f"{provider_name} กำลังประเมินหลักฐานจาก PDF ทั้งหมดร่วมกัน", progress=55)
            suffix = "" if dimensions == list(IOP_DIMENSIONS) else "_" + "-".join(dimensions)
            phase05_path = OUTPUT_DIR / f"{assessment_id}_phase05{suffix}_assessment.json"
            command = _phase05_command_arguments(
                phase04_path, phase05_path, provider, model, dimensions
            )
            _run_phase05_subprocess(
                job_id,
                command,
                phase05_path,
                provider_name,
                progress_start=55,
                progress_span=35,
            )
            paths["phase05"] = str(phase05_path)
            manifest_outputs["phase05"] = phase05_path.name

            _update_job(job_id, stage="phase06", message="กำลังสร้างสรุปรวมจากผล Phase 05", progress=94)
            _update_manifest(assessment_id, stage="phase06", outputs=manifest_outputs)
            phase05 = json.loads(phase05_path.read_text(encoding="utf-8"))
            good_examples = json.loads(GOOD_EXAMPLES_PATH.read_text(encoding="utf-8"))
            phase06 = build_summary(phase05, good_examples, "none", None)
            validate_summary_integrity(phase06)
            phase06_path = OUTPUT_DIR / f"{assessment_id}_phase06_summary.json"
            write_json_atomic(phase06_path, phase06)
            paths["phase06"] = str(phase06_path)
            manifest_outputs["phase06"] = phase06_path.name

            _update_job(job_id, stage="phase07", message="กำลังสร้างรายงานรวม", progress=98)
            _update_manifest(assessment_id, stage="phase07", outputs=manifest_outputs)
            rubric = json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))
            scoring = calculate_scoring(phase06, rubric)
            scoring_path = OUTPUT_DIR / f"{assessment_id}_phase07_scoring.json"
            write_json_atomic(scoring_path, {
                "schema_version": "ai-document.phase07-scoring/v1",
                "assessment_id": assessment_id,
                "document_id": assessment_id,
                "sources": combined04["sources"],
                "scoring": scoring,
            })
            paths["phase07_scoring"] = str(scoring_path)
            manifest_outputs["phase07_scoring"] = scoring_path.name
            extensions = ("pdf", "docx") if report_format == "both" else (report_format,)
            for extension in extensions:
                report_path = OUTPUT_DIR / f"{assessment_id}_iop_report.{extension}"
                build_report(phase06, report_path, rubric)
                paths[f"report_{extension}"] = str(report_path)
                manifest_outputs[f"report_{extension}"] = report_path.name

        _update_manifest(assessment_id, status="completed", stage="completed", outputs=manifest_outputs)
        _update_job(
            job_id,
            status="completed",
            stage="completed",
            message="ประมวลผล PDF ทั้งหมดเป็นการประเมินเดียวเสร็จแล้ว",
            progress=100,
            document_id=assessment_id,
            assessment_id=assessment_id,
            source_count=len(sources),
            page_count=sum(int(item.get("page_count") or 0) for item in sources),
            ocr_used=any(bool(item.get("ocr_used")) for item in sources),
            paths=paths,
        )
    except Exception as exc:
        _update_manifest(assessment_id, status="failed", stage="failed", error=str(exc))
        _update_job(job_id, status="failed", stage="failed", message=str(exc), progress=100, paths=paths)
    finally:
        if work_root.is_dir():
            shutil.rmtree(work_root, ignore_errors=True)


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

        phase04_settings = _web_phase04_settings()
        create_embeddings = phase04_settings.embedding_provider != "none"
        phase04_message = "กำลังแบ่งข้อความเป็น evidence chunks"
        if create_embeddings:
            phase04_message += " และสร้าง embeddings"
        _update_job(job_id, stage="phase04", message=phase04_message, progress=45)
        try:
            phase04, phase04_settings, fallback_message = _build_phase04_for_web(
                phase03,
                phase04_settings,
                phase03.get("source_file", "PDF"),
            )
            if fallback_message:
                _update_job(
                    job_id,
                    message=fallback_message,
                    retrieval_effective="lexical",
                )
        except Exception:
            _update_job(job_id, retrieval_effective="unavailable")
            raise
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
            command = _phase05_command_arguments(
                phase04_path, phase05_path, provider, model, dimensions
            )
            _run_phase05_subprocess(
                job_id,
                command,
                phase05_path,
                provider_name,
                progress_start=65,
                progress_span=29,
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
            "retrieval_requested": WEB_RETRIEVAL_MODE,
            "retrieval_effective": None,
            "top_k": WEB_LLM_TOP_K,
            "semantic_weight": WEB_SEMANTIC_WEIGHT,
            "batch_size": WEB_LLM_BATCH_SIZE,
            "max_evidence_chars": WEB_MAX_EVIDENCE_CHARS,
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


@app.post("/api/assessments", status_code=202)
async def create_multi_assessment(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    provider: str = Form("huggingface"),
    model: str | None = Form(None),
    dimensions: list[str] | None = Form(None),
    report_format: str = Form("pdf"),
) -> dict[str, str]:
    provider, model, selected_dimensions, report_format = _normalize_job_config(
        provider, model, dimensions, report_format
    )
    if len(files) < 2:
        raise HTTPException(status_code=400, detail="Multi-PDF assessment ต้องมีอย่างน้อย 2 ไฟล์")
    if len(files) > MULTI_MAX_FILES:
        raise HTTPException(status_code=413, detail=f"อัปโหลดได้สูงสุด {MULTI_MAX_FILES} ไฟล์ต่อการประเมิน")
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(INPUT_DIR).free < MULTI_MIN_FREE_BYTES:
        raise HTTPException(status_code=507, detail="พื้นที่จัดเก็บไม่เพียงพอสำหรับเริ่มการประเมิน")

    assessment_id = new_assessment_id(uuid.uuid4().hex)
    source_root = INPUT_DIR / assessment_id
    source_root.mkdir(parents=True, exist_ok=False)
    descriptors: list[dict[str, Any]] = []
    total_size = 0
    try:
        for index, upload in enumerate(files, start=1):
            try:
                filename = sanitize_pdf_filename(upload.filename)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if upload.content_type and upload.content_type.lower() not in {
                "application/pdf", "application/octet-stream"
            }:
                raise HTTPException(status_code=400, detail=f"ไฟล์ {filename} ไม่ใช่ชนิด PDF")
            sid = source_id(index)
            stored_path = source_root / f"{sid}.pdf"
            digest = hashlib.sha256()
            size = 0
            with stored_path.open("xb") as target:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    total_size += len(chunk)
                    if size > MULTI_MAX_FILE_BYTES:
                        raise HTTPException(status_code=413, detail=f"ไฟล์ {filename} มีขนาดเกินกำหนด")
                    if total_size > MULTI_MAX_TOTAL_BYTES:
                        raise HTTPException(status_code=413, detail="ขนาดไฟล์รวมเกินกำหนด")
                    digest.update(chunk)
                    target.write(chunk)
            await upload.close()
            if size == 0:
                raise HTTPException(status_code=400, detail=f"ไฟล์ {filename} ว่างเปล่า")
            try:
                with pymupdf.open(stream=stored_path.read_bytes(), filetype="pdf") as document:
                    if document.page_count < 1:
                        raise ValueError("PDF ไม่มีหน้าเอกสาร")
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"ไฟล์ {filename} เสียหายหรือไม่ใช่ PDF ที่อ่านได้") from exc
            relative = stored_path.relative_to(INPUT_DIR).as_posix()
            _record_source_name(relative, filename)
            descriptors.append({
                "source_id": sid,
                "filename": filename,
                "stored_relative_path": relative,
                "path": stored_path,
                "size_bytes": size,
                "sha256": digest.hexdigest(),
            })
    except Exception:
        for upload in files:
            await upload.close()
        shutil.rmtree(source_root, ignore_errors=True)
        _remove_source_names(assessment_id)
        raise

    config = {
        "provider": provider,
        "model": model,
        "dimensions": selected_dimensions,
        "report_format": report_format,
        "top_k": WEB_LLM_TOP_K,
        "retrieval": WEB_RETRIEVAL_MODE,
        "semantic_weight": WEB_SEMANTIC_WEIGHT,
        "batch_size": WEB_LLM_BATCH_SIZE,
        "max_evidence_chars": WEB_MAX_EVIDENCE_CHARS,
        "retrieval_fallback": WEB_RETRIEVAL_FALLBACK,
    }
    fingerprint = submission_fingerprint(descriptors, config)
    with JOBS_LOCK:
        duplicate = next(
            (
                job for job in JOBS.values()
                if job.get("submission_fingerprint") == fingerprint
                and job.get("status") in {"queued", "running"}
            ),
            None,
        )
    if duplicate:
        shutil.rmtree(source_root, ignore_errors=True)
        _remove_source_names(assessment_id)
        raise HTTPException(status_code=409, detail="ชุดไฟล์และการตั้งค่านี้กำลังประมวลผลอยู่แล้ว")

    public_sources = [{key: value for key, value in item.items() if key != "path"} for item in descriptors]
    manifest = {
        "schema_version": "ai-document.assessment-manifest/v1",
        "assessment_id": assessment_id,
        "document_id": assessment_id,
        "created_at": now_utc(),
        "updated_at": now_utc(),
        "status": "queued",
        "stage": "queued",
        "submission_fingerprint": fingerprint,
        "configuration": config,
        "sources": public_sources,
        "outputs": {},
    }
    write_json_atomic(_manifest_path(assessment_id), manifest)
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "assessment_id": assessment_id,
            "document_id": assessment_id,
            "source_name": f"{len(descriptors)} PDFs",
            "source_count": len(descriptors),
            "sources": [{"source_id": item["source_id"], "filename": item["filename"]} for item in descriptors],
            "provider": provider,
            "model": model,
            "dimensions": selected_dimensions,
            "report_format": report_format,
            "retrieval_requested": WEB_RETRIEVAL_MODE,
            "retrieval_effective": None,
            "top_k": WEB_LLM_TOP_K,
            "semantic_weight": WEB_SEMANTIC_WEIGHT,
            "batch_size": WEB_LLM_BATCH_SIZE,
            "max_evidence_chars": WEB_MAX_EVIDENCE_CHARS,
            "submission_fingerprint": fingerprint,
            "status": "queued",
            "stage": "queued",
            "message": "รอเริ่มประมวลผล PDF ทั้งหมดร่วมกัน",
            "progress": 0,
            "created_at": _now(),
            "updated_at": _now(),
            "paths": {
                "manifest": str(_manifest_path(assessment_id)),
                **{item["source_id"].lower(): str(item["path"]) for item in descriptors},
            },
        }
    background_tasks.add_task(
        _run_multi_pipeline,
        job_id,
        assessment_id,
        descriptors,
        provider,
        model,
        selected_dimensions,
        report_format,
    )
    return {"id": job_id, "assessment_id": assessment_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    return _job_payload(job_id)


@app.get("/api/jobs/{job_id}/output/{output_name}")
def download_output(job_id: str, output_name: str) -> FileResponse:
    if output_name not in {"manifest", "phase03", "phase04", "phase05", "phase06", "phase07_scoring", "report_pdf", "report_docx"}:
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
