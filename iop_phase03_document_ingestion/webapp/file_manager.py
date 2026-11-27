from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import time
from typing import Any, Callable


DOCUMENT_ID_RE = re.compile(r"^(?:DOC|ASSESSMENT)_[A-F0-9]{12}$")
FILENAME_DOCUMENT_RE = re.compile(r"^((?:DOC|ASSESSMENT)_[A-F0-9]{12})(?=$|[_.-])")
OUTPUT_EXTENSIONS = {".json", ".jsonl", ".pdf", ".docx"}
SOURCE_EXTENSIONS = {".pdf"}
PHASE_LABELS = {
    "source": "ไฟล์ต้นฉบับ",
    "phase03": "Phase 03",
    "phase04": "Phase 04",
    "phase05": "Phase 05",
    "phase06": "Phase 06",
    "phase07": "Phase 07",
    "other": "ไฟล์อื่น",
}


class FileManagerError(Exception):
    def __init__(self, message: str, *, status_code: int = 400, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class ManagedRoot:
    key: str
    path: Path
    kind: str
    extensions: frozenset[str]


@dataclass(frozen=True)
class ManagedFile:
    file_id: str
    root: ManagedRoot
    path: Path
    relative_path: str
    filename: str
    extension: str
    size: int
    modified_at: str
    mtime_ns: int
    document_id: str | None
    phase: str
    source_name: str | None
    association: str

    @property
    def is_source(self) -> bool:
        return self.root.kind == "source"

    @property
    def bulk_eligible(self) -> bool:
        return not self.is_source and self.document_id is not None and self.association != "ambiguous"


class FileManager:
    """Allow-listed inventory and deletion service.

    Opaque IDs are locators, never authorization. Every operation re-enumerates
    and revalidates root containment, file type, symlinks, access and job state.
    """

    def __init__(
        self,
        output_roots: list[Path],
        source_root: Path,
        *,
        access_token: str | None = None,
        active_document: Callable[[str | None, Path], bool] | None = None,
        source_name_for_path: Callable[[Path], str | None] | None = None,
        source_deleted: Callable[[Path], None] | None = None,
        id_secret: bytes | None = None,
        preview_ttl_seconds: int = 300,
    ) -> None:
        roots: list[ManagedRoot] = []
        for index, path in enumerate(output_roots):
            roots.append(ManagedRoot(f"output-{index}", path.resolve(), "output", frozenset(OUTPUT_EXTENSIONS)))
        roots.append(ManagedRoot("source", source_root.resolve(), "source", frozenset(SOURCE_EXTENSIONS)))
        self.roots = tuple(roots)
        self.access_token = access_token.strip() if access_token and access_token.strip() else None
        self.active_document = active_document or (lambda _document_id, _path: False)
        self.source_name_for_path = source_name_for_path or (lambda _path: None)
        self.source_deleted = source_deleted or (lambda _path: None)
        self.id_secret = id_secret or secrets.token_bytes(32)
        self.preview_ttl_seconds = preview_ttl_seconds
        self._previews: dict[str, dict[str, Any]] = {}
        self._lock = Lock()
        self.audit = logging.getLogger("webapp.file_manager.audit")
        self.audit.setLevel(logging.INFO)

    @property
    def deletion_enabled(self) -> bool:
        return bool(self.access_token)

    def authorize(self, authorization: str | None) -> None:
        if not self.access_token:
            self._audit("authorize", None, None, "denied", "deletion_disabled")
            raise FileManagerError(
                "การลบไฟล์ถูกปิดอยู่: ต้องตั้ง IOP_FILE_MANAGER_ACCESS_TOKEN ที่เซิร์ฟเวอร์",
                status_code=503,
                code="deletion_disabled",
            )
        scheme, _, credential = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not credential or not hmac.compare_digest(credential, self.access_token):
            self._audit("authorize", None, None, "denied", "unauthorized")
            raise FileManagerError("ไม่มีสิทธิ์ลบไฟล์", status_code=401, code="unauthorized")

    def inventory(self) -> dict[str, Any]:
        files = self._scan()
        active_count = 0
        records = []
        for item in files:
            active = self.active_document(item.document_id, item.path)
            active_count += int(active)
            records.append(self._public_record(item, active=active))
        output_size = sum(item.size for item in files if not item.is_source)
        source_size = sum(item.size for item in files if item.is_source)
        return {
            "files": records,
            "storage": {
                "managed_bytes": output_size + source_size,
                "generated_output_bytes": output_size,
                "source_bytes": source_size,
                "file_count": len(files),
            },
            "deletion": {
                "enabled": self.deletion_enabled,
                "reason": None if self.deletion_enabled else "ยังไม่ได้ตั้ง IOP_FILE_MANAGER_ACCESS_TOKEN ที่เซิร์ฟเวอร์",
            },
            "active_file_count": active_count,
        }

    def locate(self, file_id: str) -> ManagedFile:
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,64}", file_id or ""):
            raise FileManagerError("ไม่พบไฟล์ที่ระบุ", status_code=404, code="not_found")
        for item in self._scan():
            if hmac.compare_digest(item.file_id, file_id):
                return self._revalidate(item)
        raise FileManagerError("ไม่พบไฟล์ที่ระบุ", status_code=404, code="not_found")

    def delete_one(self, file_id: str, authorization: str | None, *, confirmed: bool) -> dict[str, Any]:
        self.authorize(authorization)
        if confirmed is not True:
            raise FileManagerError("ต้องยืนยันการลบไฟล์", code="confirmation_required")
        item = self.locate(file_id)
        self._assert_not_active(item)
        try:
            self._unlink(item, allow_source=item.is_source)
        except FileManagerError as exc:
            self._audit("delete_one", item.file_id, item.document_id, "failed", exc.code)
            raise
        if item.is_source:
            self.source_deleted(item.path)
        self._audit("delete_one", item.file_id, item.document_id, "deleted", None)
        return {"deleted": [item.file_id], "filename": item.filename}

    def bulk_preview(self, document_id: str, authorization: str | None) -> dict[str, Any]:
        self.authorize(authorization)
        document_id = self._validate_document_id(document_id)
        files = [item for item in self._scan() if item.document_id == document_id and item.bulk_eligible]
        if not files:
            raise FileManagerError("ไม่พบไฟล์ผลลัพธ์ที่ลบได้สำหรับเอกสารนี้", status_code=404, code="not_found")
        if any(self.active_document(document_id, item.path) for item in files):
            raise FileManagerError("เอกสารนี้กำลังประมวลผล จึงยังลบไม่ได้", status_code=409, code="active_job")
        fingerprints = [self._fingerprint(item) for item in files]
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._purge_previews()
            self._previews[token] = {
                "document_id": document_id,
                "expires_at": time() + self.preview_ttl_seconds,
                "fingerprints": fingerprints,
            }
        warnings = ["Source PDF จะไม่ถูกลบ"]
        if any(item.phase == "phase04" for item in files):
            warnings.append("การลบ Phase 04 ทำให้ต้องสร้าง chunks/embeddings ใหม่ก่อนรัน Phase 05 อีกครั้ง")
        self._audit("bulk_preview", None, document_id, "created", None)
        return {
            "confirmation_token": token,
            "expires_in_seconds": self.preview_ttl_seconds,
            "document_id": document_id,
            "file_count": len(files),
            "total_bytes": sum(item.size for item in files),
            "files": [self._public_record(item, active=False) for item in files],
            "warnings": warnings,
        }

    def bulk_delete(self, document_id: str, authorization: str | None, token: str, *, confirmed: bool) -> dict[str, Any]:
        self.authorize(authorization)
        document_id = self._validate_document_id(document_id)
        if confirmed is not True or not token:
            raise FileManagerError("ต้องยืนยันการลบจากรายการ Preview", code="confirmation_required")
        with self._lock:
            self._purge_previews()
            preview = self._previews.pop(token, None)
        if not preview or preview["document_id"] != document_id:
            raise FileManagerError("Preview หมดอายุหรือไม่ตรงกับเอกสาร กรุณาตรวจรายการใหม่", status_code=409, code="stale_preview")

        current = {
            item.file_id: item
            for item in self._scan()
            if item.document_id == document_id and item.bulk_eligible
        }
        expected = preview["fingerprints"]
        if len(current) != len(expected) or any(
            fingerprint["file_id"] not in current
            or self._fingerprint(current[fingerprint["file_id"]]) != fingerprint
            for fingerprint in expected
        ):
            raise FileManagerError("รายการไฟล์เปลี่ยนไปหลัง Preview กรุณาตรวจรายการใหม่", status_code=409, code="stale_preview")
        if any(self.active_document(document_id, item.path) for item in current.values()):
            raise FileManagerError("เอกสารนี้กำลังประมวลผล จึงยังลบไม่ได้", status_code=409, code="active_job")

        deleted: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        for fingerprint in expected:
            item = current[fingerprint["file_id"]]
            try:
                item = self._revalidate(item)
                self._assert_not_active(item)
                self._unlink(item)
                deleted.append({"file_id": item.file_id, "filename": item.filename})
                self._audit("bulk_delete", item.file_id, document_id, "deleted", None)
            except (FileManagerError, OSError) as exc:
                code = exc.code if isinstance(exc, FileManagerError) else "filesystem_error"
                failed.append({"file_id": item.file_id, "filename": item.filename, "error": code})
                self._audit("bulk_delete", item.file_id, document_id, "failed", code)
        return {
            "document_id": document_id,
            "deleted": deleted,
            "failed": failed,
            "partial": bool(failed),
        }

    def _scan(self) -> list[ManagedFile]:
        preliminary: list[dict[str, Any]] = []
        source_map: dict[str, str] = {}
        for root in self.roots:
            if not root.path.is_dir():
                continue
            for path in root.path.rglob("*"):
                if not path.is_file() or path.is_symlink() or path.suffix.lower() not in root.extensions:
                    continue
                try:
                    safe_path = self._safe_path(root, path)
                    stat = safe_path.stat()
                except (FileManagerError, OSError):
                    continue
                metadata = self._read_identity_metadata(safe_path) if safe_path.suffix.lower() == ".json" else {}
                filename_id = self._filename_document_id(safe_path.name)
                metadata_id = metadata.get("document_id")
                association = "metadata" if metadata_id else ("filename" if filename_id else "unclassified")
                document_id = metadata_id or filename_id
                if metadata_id and filename_id and metadata_id != filename_id:
                    document_id = None
                    association = "ambiguous"
                stored_source_name = metadata.get("source_file")
                source_name = stored_source_name
                if root.kind == "output" and stored_source_name:
                    source_root = next((candidate for candidate in self.roots if candidate.kind == "source"), None)
                    if source_root:
                        source_name = self.source_name_for_path(source_root.path / stored_source_name) or stored_source_name
                if root.kind == "output" and document_id and source_name and Path(source_name).name == source_name:
                    source_map.setdefault(stored_source_name or source_name, document_id)
                if root.kind == "output" and document_id:
                    for source in metadata.get("sources", []):
                        if not isinstance(source, dict):
                            continue
                        relative_source = source.get("stored_relative_path")
                        if (
                            isinstance(relative_source, str)
                            and not Path(relative_source).is_absolute()
                            and ".." not in Path(relative_source).parts
                        ):
                            source_map.setdefault(Path(relative_source).as_posix(), document_id)
                preliminary.append({
                    "root": root, "path": safe_path, "stat": stat, "metadata": metadata,
                    "document_id": document_id, "source_name": source_name, "association": association,
                })

        results: list[ManagedFile] = []
        for row in preliminary:
            root = row["root"]
            path = row["path"]
            document_id = row["document_id"]
            association = row["association"]
            if root.kind == "source":
                relative = path.relative_to(root.path).as_posix()
                mapped = source_map.get(relative) or source_map.get(path.name)
                document_id = mapped
                association = "metadata-source" if mapped else "unclassified"
                row["source_name"] = self.source_name_for_path(path) or path.name
            relative = path.relative_to(root.path).as_posix()
            stat = row["stat"]
            results.append(ManagedFile(
                file_id=self._file_id(root, relative), root=root, path=path, relative_path=relative,
                filename=path.name, extension=path.suffix.lower().lstrip("."), size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                mtime_ns=stat.st_mtime_ns, document_id=document_id,
                phase="source" if root.kind == "source" else self._phase(path.name, row["metadata"]),
                source_name=row["source_name"], association=association,
            ))
        return sorted(results, key=lambda item: (item.document_id or "~", item.phase, item.filename.lower()))

    def _read_identity_metadata(self, path: Path) -> dict[str, Any]:
        # Identity fields are emitted near the beginning of every pipeline JSON.
        # Read a bounded prefix for large evidence/embedding payloads rather than
        # loading tens of MB on each sidebar refresh.
        if path.stat().st_size > 2_000_000:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    prefix = handle.read(131_072)
            except (OSError, UnicodeError):
                return {}
            document_match = re.search(r'"document_id"\s*:\s*"((?:DOC|ASSESSMENT)_[A-F0-9]{12})"', prefix)
            source_match = re.search(r'"source_file"\s*:\s*"([^"\\/]+)"', prefix)
            return {
                key: value for key, value in {
                    "document_id": document_match.group(1) if document_match else None,
                    "source_file": source_match.group(1) if source_match else None,
                }.items() if value
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        document_id = payload.get("assessment_id") or payload.get("document_id")
        if not isinstance(document_id, str) or not DOCUMENT_ID_RE.fullmatch(document_id):
            document_id = None
        source = payload.get("source_file")
        if not isinstance(source, str):
            metadata = payload.get("metadata")
            source = metadata.get("source_file") if isinstance(metadata, dict) else None
        if not isinstance(source, str) or Path(source).name != source:
            source = None
        sources = payload.get("sources")
        if not isinstance(sources, list):
            sources = []
        return {
            key: value
            for key, value in {
                "document_id": document_id,
                "source_file": source,
                "sources": sources,
            }.items()
            if value
        }

    def _filename_document_id(self, name: str) -> str | None:
        match = FILENAME_DOCUMENT_RE.match(name)
        return match.group(1) if match else None

    def _phase(self, name: str, metadata: dict[str, str]) -> str:
        lower = name.lower()
        for number in ("04", "05", "06", "07"):
            if re.search(rf"(?:^|[_-])phase{number}(?:$|[_\-.])", lower):
                return f"phase{number}"
        if "_iop_report." in lower or re.search(r"(?:^|[_-])report(?:$|[_\-.])", lower):
            return "phase07"
        if re.search(r"(?:^|[_-])phase03(?:$|[_\-.])", lower):
            return "phase03"
        if metadata.get("document_id") and re.fullmatch(r"DOC_[A-F0-9]{12}\.json", name):
            return "phase03"
        return "other"

    def _file_id(self, root: ManagedRoot, relative: str) -> str:
        digest = hmac.new(self.id_secret, f"{root.key}:{relative}".encode(), hashlib.sha256).hexdigest()
        return digest[:40]

    def _safe_path(self, root: ManagedRoot, path: Path) -> Path:
        if path.is_symlink():
            raise FileManagerError("ไม่อนุญาต symlink", status_code=403, code="symlink")
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root.path)
        except (OSError, ValueError) as exc:
            raise FileManagerError("ไฟล์อยู่นอก managed root", status_code=403, code="outside_root") from exc
        if resolved.suffix.lower() not in root.extensions:
            raise FileManagerError("ชนิดไฟล์ไม่ได้รับอนุญาต", status_code=403, code="unsafe_type")
        cursor = resolved.parent
        while cursor != root.path:
            if cursor.is_symlink():
                raise FileManagerError("ไม่อนุญาต symlink", status_code=403, code="symlink")
            if root.path not in cursor.parents:
                raise FileManagerError("ไฟล์อยู่นอก managed root", status_code=403, code="outside_root")
            cursor = cursor.parent
        return resolved

    def _revalidate(self, item: ManagedFile) -> ManagedFile:
        path = self._safe_path(item.root, item.path)
        stat = path.stat()
        if stat.st_size != item.size or stat.st_mtime_ns != item.mtime_ns:
            raise FileManagerError("ไฟล์เปลี่ยนแปลงแล้ว กรุณารีเฟรช", status_code=409, code="changed")
        return item

    def _unlink(self, item: ManagedFile, *, allow_source: bool = False) -> None:
        item = self._revalidate(item)
        allowed_kinds = {"output", "source"} if allow_source else {"output"}
        if item.root.kind not in allowed_kinds:
            raise FileManagerError("ไม่อนุญาตให้ลบไฟล์ประเภทนี้", status_code=403, code="source_protected")
        try:
            item.path.unlink()
        except FileNotFoundError as exc:
            raise FileManagerError("ไม่พบไฟล์แล้ว", status_code=404, code="not_found") from exc
        except PermissionError as exc:
            raise FileManagerError("ไม่มีสิทธิ์ลบไฟล์บนระบบ", status_code=403, code="permission_denied") from exc
        except OSError as exc:
            raise FileManagerError("ระบบไฟล์ไม่สามารถลบไฟล์ได้", status_code=500, code="filesystem_error") from exc

    def _assert_not_active(self, item: ManagedFile) -> None:
        if self.active_document(item.document_id, item.path):
            raise FileManagerError("ไฟล์นี้กำลังถูกใช้งานโดยงานประมวลผล", status_code=409, code="active_job")

    def _fingerprint(self, item: ManagedFile) -> dict[str, Any]:
        return {"file_id": item.file_id, "size": item.size, "mtime_ns": item.mtime_ns}

    def _public_record(self, item: ManagedFile, *, active: bool) -> dict[str, Any]:
        delete_status = "active_job" if active else "eligible"
        return {
            "file_id": item.file_id,
            "document_id": item.document_id,
            "phase": item.phase,
            "phase_label": PHASE_LABELS[item.phase],
            "filename": item.filename,
            "original_filename": item.source_name,
            "extension": item.extension,
            "size": item.size,
            "modified_at": item.modified_at,
            "is_source": item.is_source,
            "active": active,
            "deletable": self.deletion_enabled and delete_status == "eligible",
            "delete_status": delete_status,
        }

    def _validate_document_id(self, value: str) -> str:
        if not DOCUMENT_ID_RE.fullmatch(value or ""):
            raise FileManagerError("Document/Assessment ID ไม่ถูกต้อง", code="invalid_document_id")
        return value

    def _purge_previews(self) -> None:
        now = time()
        for token in [key for key, value in self._previews.items() if value["expires_at"] <= now]:
            self._previews.pop(token, None)

    def _audit(self, operation: str, file_id: str | None, document_id: str | None, outcome: str, error: str | None) -> None:
        self.audit.info(
            "file_manager operation=%s actor=shared-token file_id=%s document_id=%s outcome=%s error=%s",
            operation, file_id or "-", document_id or "-", outcome, error or "-",
        )
