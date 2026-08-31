import copy

import pytest

from phase06.summary import build_summary
from phase06.validation import validate_summary_integrity


def _good_examples():
    return {"knowledge_base": {"name": "Examples", "version": "1", "rules": {"affects_score": False, "affects_classification": False, "used_for_summary_style": True}, "overall_example": {"structure": ["Executive Summary", "Key Strengths"]}, "dimensions": [{"dimension_id": "D1", "name_th": "ยุทธศาสตร์", "summary_style": {"structure": ["Vision", "KPI"]}, "criteria": [{"extracted_pattern": "Vision KPI", "evidence_pattern": "vision KPI"}]}]}}


def _phase05():
    evidence = {"chunk_id": "C1", "page_start": 2, "page_end": 2, "source_blocks": [{"block_id": "P2_B1", "page": 2}], "text": "หลักฐาน"}
    return {"schema_version": "ai-document.phase05/v1", "document_id": "DOC_1", "dimensions": [{"dimension_id": "D1", "criteria": [{"criterion": {"criterion_id": "1.1.1", "component_id": "1.1", "criterion": "เกณฑ์", "dimension_name": "ยุทธศาสตร์"}, "evidence": [evidence], "assessment": {"score": 3, "confidence": 0.7, "rationale_th": "มีหลักฐานตามที่ประเมิน", "strengths": ["มีนโยบาย"], "missing_points": ["ไม่มี KPI"]}, "status": "assessed"}]}]}


def test_template_summary_preserves_phase05_snapshot_and_citations():
    source = _phase05()
    summary = build_summary(source, _good_examples())
    item = summary["dimension_summaries"][0]["criterion_summaries"][0]
    assert item["score"] == 3
    assert item["citations"][0]["chunk_id"] == "C1"
    assert item["phase05_snapshot"] == source["dimensions"][0]["criteria"][0]
    validate_summary_integrity(summary)


def test_integrity_rejects_changed_score_or_non_source_citation():
    summary = build_summary(_phase05(), _good_examples())
    changed = copy.deepcopy(summary)
    changed["dimension_summaries"][0]["criterion_summaries"][0]["phase05_snapshot"]["assessment"]["score"] = 5
    with pytest.raises(ValueError, match="change"):
        validate_summary_integrity(changed)
    changed_source = copy.deepcopy(summary)
    changed_source["source_assessment"]["criterion_snapshots"]["1.1.1"]["assessment"]["score"] = 5
    with pytest.raises(ValueError, match="digest"):
        validate_summary_integrity(changed_source)
    bad_citation = copy.deepcopy(summary)
    bad_citation["dimension_summaries"][0]["criterion_summaries"][0]["citations"] = [{"chunk_id": "NEW"}]
    with pytest.raises(ValueError, match="not Phase 05 evidence"):
        validate_summary_integrity(bad_citation)


def test_none_assessment_creates_prompt_ready_template_without_score():
    phase05 = _phase05()
    phase05["dimensions"][0]["criteria"][0]["assessment"] = None
    phase05["dimensions"][0]["criteria"][0]["status"] = "ready_for_human_or_llm_review"
    summary = build_summary(phase05, _good_examples())
    item = summary["dimension_summaries"][0]["criterion_summaries"][0]
    assert item["score"] is None
    assert "ยังไม่มีผลคะแนน" in item["summary"]
