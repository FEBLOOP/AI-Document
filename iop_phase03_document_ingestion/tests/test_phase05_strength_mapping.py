from __future__ import annotations

import json

import pytest

from llm import LLMSettings
from phase05 import assessment as assessment_module
from phase05.assessment import assess_dimension_with_llm, assess_with_llm
from phase05.cli import _resume_items


SETTINGS = LLMSettings("openai", "test-model", None, "test", 512, False)


def _packet(criterion_id: str = "1.1.1") -> dict:
    return {
        "criterion": {"criterion_id": criterion_id},
        "evidence": [
            {"chunk_id": "C1", "text": "หลักฐานหนึ่ง"},
            {"chunk_id": "C2", "text": "หลักฐานสอง"},
        ],
    }


def _assessment(criterion_id: str | None = None) -> dict:
    value = {
        "score": 3,
        "confidence": 0.8,
        "rationale_th": "มีหลักฐานสนับสนุน",
        "evidence_chunk_ids": ["C1", "C2"],
        "matched_points": [],
        "strengths": ["มีนโยบาย", "มีคณะทำงาน"],
        "strength_evidence_mapping": [
            {"strength": "มีนโยบาย", "evidence_chunk_ids": ["C1", "C2"]},
            {"strength": "มีคณะทำงาน", "evidence_chunk_ids": ["C1"]},
        ],
        "missing_points": [],
    }
    if criterion_id is not None:
        value["criterion_id"] = criterion_id
    return value


def test_single_assessment_supports_many_to_many_mapping(monkeypatch) -> None:
    monkeypatch.setattr(assessment_module, "complete_json", lambda *_args, **_kwargs: _assessment())

    result = assess_with_llm(_packet(), SETTINGS, top_k=2)

    assert result["strength_evidence_mapping"] == [
        {"strength": "มีนโยบาย", "evidence_chunk_ids": ["C1", "C2"]},
        {"strength": "มีคณะทำงาน", "evidence_chunk_ids": ["C1"]},
    ]


def test_duplicate_mapping_entries_and_links_are_normalized(monkeypatch) -> None:
    response = _assessment()
    response["strength_evidence_mapping"] = [
        {"strength": "มีนโยบาย", "evidence_chunk_ids": ["C1", "C1"]},
        {"strength": "มีคณะทำงาน", "evidence_chunk_ids": []},
        {"strength": "มีนโยบาย", "evidence_chunk_ids": ["C2", "C1"]},
    ]
    monkeypatch.setattr(assessment_module, "complete_json", lambda *_args, **_kwargs: response)

    result = assess_with_llm(_packet(), SETTINGS, top_k=2)

    assert result["strength_evidence_mapping"] == [
        {"strength": "มีนโยบาย", "evidence_chunk_ids": ["C1", "C2"]},
        {"strength": "มีคณะทำงาน", "evidence_chunk_ids": []},
    ]


@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        ([{"strength": "จุดแข็งที่ไม่มี", "evidence_chunk_ids": ["C1"]}], "unknown strength"),
        (
            [
                {"strength": "มีนโยบาย", "evidence_chunk_ids": ["C3"]},
                {"strength": "มีคณะทำงาน", "evidence_chunk_ids": []},
            ],
            "unselected evidence chunk",
        ),
    ],
)
def test_unknown_strength_and_uncited_chunk_are_rejected(monkeypatch, mapping, message) -> None:
    response = _assessment()
    response["strength_evidence_mapping"] = mapping
    monkeypatch.setattr(assessment_module, "complete_json", lambda *_args, **_kwargs: response)

    with pytest.raises(ValueError, match=message):
        assess_with_llm(_packet(), SETTINGS, top_k=2)


def test_batch_retries_invalid_mapping_then_accepts_complete_mapping(monkeypatch) -> None:
    invalid = _assessment("1.1.1")
    invalid.pop("strength_evidence_mapping")
    valid = _assessment("1.1.1")
    responses = iter([{"assessments": [invalid]}, {"assessments": [valid]}])
    monkeypatch.setattr(
        assessment_module,
        "complete_json",
        lambda *_args, **_kwargs: next(responses),
    )
    retries = []

    result = assess_dimension_with_llm(
        [_packet()],
        SETTINGS,
        max_attempts=2,
        on_retry=lambda attempt, error: retries.append((attempt, str(error))),
        top_k=2,
    )

    assert result[0]["strength_evidence_mapping"][0]["evidence_chunk_ids"] == ["C1", "C2"]
    assert retries and retries[0][0] == 2
    assert "strength_evidence_mapping" in retries[0][1]


def test_resume_accepts_completed_legacy_assessment_without_mapping(tmp_path) -> None:
    old_item = {
        "criterion": {"criterion_id": "1.1.1"},
        "assessment": {"score": 3, "strengths": ["มีนโยบาย"]},
        "status": "assessed",
    }
    path = tmp_path / "phase05.json"
    path.write_text(
        json.dumps(
            {
                "document_id": "DOC_TEST",
                "assessment_provider": "openai",
                "assessment_model": "test-model",
                "created_at": "2026-01-01T00:00:00Z",
                "dimensions": [{"dimension_id": "D1", "criteria": [old_item]}],
            }
        ),
        encoding="utf-8",
    )

    completed, created_at = _resume_items(path, {"document_id": "DOC_TEST"}, SETTINGS)

    assert completed["1.1.1"] == old_item
    assert created_at == "2026-01-01T00:00:00Z"
