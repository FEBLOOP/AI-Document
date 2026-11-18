from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from webapp.file_manager import FileManager, FileManagerError


DOC_A = "DOC_AAAAAAAAAAAA"
DOC_B = "DOC_AAAAAAAAAAAB"
AUTH = "Bearer test-shared-token"
SECRET = b"test-id-secret"


def _write_json(path: Path, document_id: str, **extra: object) -> None:
    path.write_text(json.dumps({"document_id": document_id, **extra}), encoding="utf-8")


def _manager(tmp_path: Path, *, token: str | None = "test-shared-token", active=None, source_name=None) -> tuple[FileManager, Path, Path]:
    output = tmp_path / "output"
    source = tmp_path / "uploads"
    output.mkdir()
    source.mkdir()
    manager = FileManager(
        [output], source, access_token=token, active_document=active,
        source_name_for_path=source_name,
        id_secret=SECRET, preview_ttl_seconds=60,
    )
    return manager, output, source


def test_inventory_groups_multiple_variants_and_similar_document_ids(tmp_path: Path) -> None:
    manager, output, source = _manager(tmp_path)
    _write_json(output / f"{DOC_A}.json", DOC_A, source_file="source-a.pdf")
    _write_json(output / f"{DOC_A}_phase04_hybrid.json", DOC_A)
    (output / f"{DOC_A}_phase05_D1_claude.json").write_text("{}", encoding="utf-8")
    (output / f"{DOC_A}_phase07_report.pdf").write_bytes(b"pdf")
    (output / f"{DOC_A}_phase07_report.docx").write_bytes(b"docx")
    _write_json(output / f"{DOC_B}_phase04.json", DOC_B)
    (source / "source-a.pdf").write_bytes(b"source")

    first = manager.inventory()["files"]
    second_manager = FileManager([output], source, access_token="test-shared-token", id_secret=SECRET)
    second = second_manager.inventory()["files"]

    assert len(first) == 7
    assert {row["file_id"] for row in first} == {row["file_id"] for row in second}
    assert len([row for row in first if row["document_id"] == DOC_A]) == 6
    assert len([row for row in first if row["document_id"] == DOC_B]) == 1
    assert {row["phase"] for row in first if row["document_id"] == DOC_A} >= {
        "source", "phase03", "phase04", "phase05", "phase07"
    }


def test_ambiguous_identity_is_uncategorized_and_not_bulk_eligible(tmp_path: Path) -> None:
    manager, output, _source = _manager(tmp_path)
    _write_json(output / f"{DOC_A}_phase05.json", DOC_B)

    [record] = manager.inventory()["files"]

    assert record["document_id"] is None
    preview_error = pytest.raises(FileManagerError, manager.bulk_preview, DOC_A, AUTH)
    assert preview_error.value.code == "not_found"


def test_large_json_still_checks_metadata_for_ambiguous_identity(tmp_path: Path) -> None:
    manager, output, _source = _manager(tmp_path)
    target = output / f"{DOC_A}_phase04.json"
    target.write_text(
        json.dumps({"document_id": DOC_B, "padding": "x" * 2_100_000}),
        encoding="utf-8",
    )

    [record] = manager.inventory()["files"]

    assert record["document_id"] is None


def test_source_original_filename_callback_is_exposed_for_search(tmp_path: Path) -> None:
    manager, output, source = _manager(
        tmp_path,
        source_name=lambda path: "เอกสารต้นฉบับ.pdf" if path.name == "stored.pdf" else None,
    )
    _write_json(output / f"{DOC_A}.json", DOC_A, source_file="stored.pdf")
    (source / "stored.pdf").write_bytes(b"source")

    records = manager.inventory()["files"]

    assert all(row["original_filename"] == "เอกสารต้นฉบับ.pdf" for row in records)


def test_allow_lists_hide_unsafe_and_outside_files(tmp_path: Path) -> None:
    manager, output, source = _manager(tmp_path)
    (output / "secret.env").write_text("SECRET=x", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (source / "notes.txt").write_text("not managed", encoding="utf-8")

    assert manager.inventory()["files"] == []
    with pytest.raises(FileManagerError) as forged:
        manager.locate("../config.json")
    assert forged.value.code == "not_found"


def test_symlink_escape_is_not_in_inventory(tmp_path: Path) -> None:
    manager, output, _source = _manager(tmp_path)
    outside = tmp_path / "outside.json"
    _write_json(outside, DOC_A)
    link = output / f"{DOC_A}_phase04.json"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are unavailable for this Windows test account")

    assert manager.inventory()["files"] == []


def test_deletion_fails_closed_without_access_configuration(tmp_path: Path) -> None:
    manager, output, _source = _manager(tmp_path, token=None)
    _write_json(output / f"{DOC_A}.json", DOC_A)
    [record] = manager.inventory()["files"]

    assert record["deletable"] is False
    with pytest.raises(FileManagerError) as error:
        manager.delete_one(record["file_id"], AUTH, confirmed=True)
    assert error.value.code == "deletion_disabled"
    assert (output / f"{DOC_A}.json").exists()


def test_single_delete_requires_auth_confirmation_and_protects_source(tmp_path: Path) -> None:
    manager, output, source = _manager(tmp_path)
    _write_json(output / f"{DOC_A}.json", DOC_A, source_file="original.pdf")
    (source / "original.pdf").write_bytes(b"source")
    records = manager.inventory()["files"]
    generated = next(row for row in records if not row["is_source"])
    source_record = next(row for row in records if row["is_source"])

    with pytest.raises(FileManagerError) as unauthenticated:
        manager.delete_one(generated["file_id"], None, confirmed=True)
    assert unauthenticated.value.code == "unauthorized"
    with pytest.raises(FileManagerError) as unconfirmed:
        manager.delete_one(generated["file_id"], AUTH, confirmed=False)
    assert unconfirmed.value.code == "confirmation_required"
    with pytest.raises(FileManagerError) as protected:
        manager.delete_one(source_record["file_id"], AUTH, confirmed=True)
    assert protected.value.code == "source_protected"

    result = manager.delete_one(generated["file_id"], AUTH, confirmed=True)
    assert result["filename"] == f"{DOC_A}.json"
    assert not (output / f"{DOC_A}.json").exists()
    assert (source / "original.pdf").exists()


def test_bulk_preview_and_delete_are_exact_and_never_include_source(tmp_path: Path) -> None:
    manager, output, source = _manager(tmp_path)
    _write_json(output / f"{DOC_A}.json", DOC_A, source_file="original.pdf")
    _write_json(output / f"{DOC_A}_phase04.json", DOC_A)
    _write_json(output / f"{DOC_B}_phase04.json", DOC_B)
    (source / "original.pdf").write_bytes(b"source")

    preview = manager.bulk_preview(DOC_A, AUTH)

    assert preview["file_count"] == 2
    assert all(not row["is_source"] for row in preview["files"])
    assert any("Phase 04" in warning for warning in preview["warnings"])
    result = manager.bulk_delete(DOC_A, AUTH, preview["confirmation_token"], confirmed=True)
    assert len(result["deleted"]) == 2
    assert result["failed"] == []
    assert (output / f"{DOC_B}_phase04.json").exists()
    assert (source / "original.pdf").exists()


def test_bulk_delete_rejects_stale_preview(tmp_path: Path) -> None:
    manager, output, _source = _manager(tmp_path)
    target = output / f"{DOC_A}_phase04.json"
    _write_json(target, DOC_A)
    preview = manager.bulk_preview(DOC_A, AUTH)
    target.write_text(json.dumps({"document_id": DOC_A, "changed": True}), encoding="utf-8")

    with pytest.raises(FileManagerError) as stale:
        manager.bulk_delete(DOC_A, AUTH, preview["confirmation_token"], confirmed=True)
    assert stale.value.code == "stale_preview"
    assert target.exists()


def test_active_job_blocks_single_and_bulk_delete(tmp_path: Path) -> None:
    manager, output, _source = _manager(tmp_path, active=lambda document_id, _path: document_id == DOC_A)
    _write_json(output / f"{DOC_A}_phase04.json", DOC_A)
    [record] = manager.inventory()["files"]

    assert record["active"] is True
    with pytest.raises(FileManagerError) as single:
        manager.delete_one(record["file_id"], AUTH, confirmed=True)
    assert single.value.code == "active_job"
    with pytest.raises(FileManagerError) as bulk:
        manager.bulk_preview(DOC_A, AUTH)
    assert bulk.value.code == "active_job"


def test_preview_token_is_one_time_and_bound_to_document(tmp_path: Path) -> None:
    manager, output, _source = _manager(tmp_path)
    _write_json(output / f"{DOC_A}_phase04.json", DOC_A)
    _write_json(output / f"{DOC_B}_phase04.json", DOC_B)
    preview = manager.bulk_preview(DOC_A, AUTH)

    with pytest.raises(FileManagerError) as wrong_document:
        manager.bulk_delete(DOC_B, AUTH, preview["confirmation_token"], confirmed=True)
    assert wrong_document.value.code == "stale_preview"
    assert (output / f"{DOC_A}_phase04.json").exists()


def test_frontend_contains_refresh_hooks_and_preserves_assessment_controls() -> None:
    html = (Path(__file__).parents[1] / "webapp" / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'id="upload-form"' in html
    assert 'id="provider"' in html
    assert 'id="dimensions-field"' in html
    assert 'id="file-sidebar"' in html
    assert "loadFiles();" in html
    assert "if (response.ok) await loadFiles()" in html
