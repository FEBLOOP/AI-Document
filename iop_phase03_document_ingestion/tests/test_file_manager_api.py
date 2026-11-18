from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

import webapp.main as web_main
from webapp.file_manager import FileManager


DOC_ID = "DOC_CCCCCCCCCCCC"
AUTH = {"Authorization": "Bearer api-test-token"}


def test_source_original_filename_index_persists(tmp_path: Path, monkeypatch) -> None:
    index_path = tmp_path / "uploads" / ".file-manager-metadata.json"
    index_path.parent.mkdir()
    monkeypatch.setattr(web_main, "SOURCE_INDEX_PATH", index_path)

    web_main._record_source_name("stored-id.pdf", "ต้นฉบับ.pdf")

    assert web_main._source_original_name(index_path.parent / "stored-id.pdf") == "ต้นฉบับ.pdf"
    assert json.loads(index_path.read_text(encoding="utf-8"))["stored-id.pdf"] == "ต้นฉบับ.pdf"


def test_file_manager_api_inventory_download_and_guarded_delete(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "output"
    source = tmp_path / "uploads"
    output.mkdir()
    source.mkdir()
    target = output / f"{DOC_ID}_phase04.json"
    target.write_text(json.dumps({"document_id": DOC_ID}), encoding="utf-8")
    manager = FileManager([output], source, access_token="api-test-token", id_secret=b"api-test-secret")
    monkeypatch.setattr(web_main, "FILE_MANAGER", manager)

    inventory = web_main.list_managed_files()
    [record] = inventory["files"]
    assert "path" not in record

    download = web_main.download_managed_file(record["file_id"])
    assert Path(download.path) == target

    with pytest.raises(HTTPException) as denied:
        web_main.delete_managed_file(record["file_id"], {"confirmed": True}, None)
    assert denied.value.status_code == 401
    assert target.exists()

    with pytest.raises(HTTPException) as unconfirmed:
        web_main.delete_managed_file(record["file_id"], {"confirmed": False}, AUTH["Authorization"])
    assert unconfirmed.value.status_code == 400
    assert target.exists()

    deleted = web_main.delete_managed_file(
        record["file_id"], {"confirmed": True}, AUTH["Authorization"]
    )
    assert deleted["filename"] == target.name
    assert not target.exists()


def test_bulk_api_requires_preview_token(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "output"
    source = tmp_path / "uploads"
    output.mkdir()
    source.mkdir()
    (output / f"{DOC_ID}_phase05_run-a.json").write_text("{}", encoding="utf-8")
    (output / f"{DOC_ID}_phase06_run-a.json").write_text("{}", encoding="utf-8")
    manager = FileManager([output], source, access_token="api-test-token", id_secret=b"api-test-secret")
    monkeypatch.setattr(web_main, "FILE_MANAGER", manager)

    with pytest.raises(HTTPException) as no_preview:
        web_main.delete_document_outputs(
            DOC_ID,
            {"confirmed": True, "confirmation_token": "forged"},
            AUTH["Authorization"],
        )
    assert no_preview.value.status_code == 409

    payload = web_main.preview_document_output_deletion(DOC_ID, AUTH["Authorization"])
    assert payload["file_count"] == 2

    deleted = web_main.delete_document_outputs(
        DOC_ID,
        {"confirmed": True, "confirmation_token": payload["confirmation_token"]},
        AUTH["Authorization"],
    )
    assert len(deleted["deleted"]) == 2
