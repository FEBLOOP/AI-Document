import json

import pytest

from llm.config import LLMSettings
from phase05.assessment import assess_dimension_with_llm, assess_with_llm, review_packet
from phase05.cli import _build_output, _parse_dimensions, _resume_items, _write_output_atomic
from phase05.retrieval import evidence_item, query_embedder_from_chunks, retrieve
from phase05.rubric import load_criteria
from phase05.rubric import load_scoring_guide


def test_retrieval_returns_matching_chunk():
    hits = retrieve([{"chunk_id": "C1", "normalized_text": "นโยบายด้านนวัตกรรมและผู้บริหาร"}, {"chunk_id": "C2", "normalized_text": "การเงินบัญชี"}], "ผู้บริหาร นวัตกรรม", 1)
    assert hits[0]["chunk"]["chunk_id"] == "C1"


def test_hybrid_retrieval_combines_semantic_and_lexical_scores():
    chunks = [
        {
            "chunk_id": "LEXICAL",
            "normalized_text": "นโยบายผู้บริหาร",
            "embedding": {"provider": "hash", "model": "test", "vector": [0.0, 1.0]},
        },
        {
            "chunk_id": "SEMANTIC",
            "normalized_text": "ข้อความที่ใช้คำต่างกัน",
            "embedding": {"provider": "hash", "model": "test", "vector": [1.0, 0.0]},
        },
    ]

    hits = retrieve(
        chunks,
        "นโยบายผู้บริหาร",
        2,
        mode="hybrid",
        query_vector=[1.0, 0.0],
        semantic_weight=0.7,
    )

    assert hits[0]["chunk"]["chunk_id"] == "SEMANTIC"
    assert hits[0]["semantic_score"] == 1.0
    assert hits[1]["lexical_score"] > 0


def test_evidence_item_preserves_hybrid_score_breakdown():
    item = evidence_item(
        {
            "score": 0.8,
            "lexical_score": 0.4,
            "semantic_score": 0.9,
            "chunk": {"chunk_id": "C1", "normalized_text": "หลักฐาน"},
        }
    )

    assert item["relevance_score"] == 0.8
    assert item["lexical_score"] == 0.4
    assert item["semantic_score"] == 0.9


def test_query_embedder_uses_phase04_embedding_metadata(monkeypatch):
    captured = {}
    expected = object()

    def fake_create_embedder(provider, model, dimensions):
        captured.update(
            provider=provider,
            model=model,
            dimensions=dimensions,
        )
        return expected

    monkeypatch.setattr("phase05.retrieval.create_embedder", fake_create_embedder)
    chunks = [
        {
            "embedding": {
                "provider": "sentence_transformers",
                "model": "multilingual-test",
                "vector": [0.1, 0.2, 0.3],
            }
        }
    ]

    result = query_embedder_from_chunks(chunks)

    assert result is expected
    assert captured == {
        "provider": "sentence_transformers",
        "model": "multilingual-test",
        "dimensions": 3,
    }


def test_iop_hierarchy_is_flattened():
    rubric = {"dimensions": [{"id": "D1", "components": [{"id": "1.1", "criteria": [{"id": "1.1.1", "description": "x"}]}]}]}
    assert load_criteria(rubric)[0]["criterion_id"] == "1.1.1"


def test_scoring_guide_is_loaded_from_rubric():
    guide = {str(score): f"rubric level {score}" for score in range(1, 6)}
    assert load_scoring_guide({"scoring_guide": guide}) == guide


def test_dimension_prompt_uses_supplied_rubric_and_forbids_evidence_count_scoring(monkeypatch):
    captured = {}

    def fake_complete_json(settings, messages):
        captured["messages"] = messages
        return {
            "assessments": [
                {
                    "criterion_id": "1.1.1",
                    "score": 3,
                    "confidence": 0.7,
                    "evidence_sufficiency": "sufficient",
                    "evidence_chunk_ids": [],
                    "matched_points": [],
                    "strengths": [],
                    "missing_points": [],
                }
            ]
        }

    monkeypatch.setattr("phase05.assessment.complete_json", fake_complete_json)
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)
    guide = {str(score): f"custom level {score}" for score in range(1, 6)}

    assess_dimension_with_llm(
        [{"criterion": {"criterion_id": "1.1.1"}, "evidence": []}],
        settings,
        scoring_guide=guide,
    )

    assert "custom level 5" in captured["messages"][1]["content"]
    assert "Evidence count alone must never determine a score" in captured["messages"][0]["content"]


def test_dimension_selection_is_normalized_and_deduplicated():
    assert _parse_dimensions("d5, D1, d5") == ["D5", "D1"]
    assert _parse_dimensions(None) is None


def test_dimension_assessment_uses_one_call_and_preserves_criterion_order(monkeypatch):
    calls = []

    def fake_complete_json(settings, messages):
        calls.append((settings, messages))
        return {
            "assessments": [
                {"criterion_id": "1.1.2", "score": 4, "confidence": 0.8, "evidence_sufficiency": "sufficient", "evidence_chunk_ids": [], "matched_points": [], "strengths": [], "missing_points": []},
                {"criterion_id": "1.1.1", "score": 3, "confidence": 0.7, "evidence_sufficiency": "sufficient", "evidence_chunk_ids": [], "matched_points": [], "strengths": [], "missing_points": []},
            ]
        }

    monkeypatch.setattr("phase05.assessment.complete_json", fake_complete_json)
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)
    packets = [
        {"criterion": {"criterion_id": "1.1.1"}, "evidence": []},
        {"criterion": {"criterion_id": "1.1.2"}, "evidence": []},
    ]

    result = assess_dimension_with_llm(packets, settings)

    assert len(calls) == 1
    assert [item["criterion_id"] for item in result] == ["1.1.1", "1.1.2"]


def test_review_packet_limits_prompt_evidence_without_mutating_output():
    evidence = [{"chunk_id": "C1", "text": "ก" * 20, "source_blocks": [{"block_id": "B1"}]}]

    packet = review_packet(
        {"criterion_id": "1.1.1"},
        evidence,
        max_evidence_chars=5,
    )

    assert packet["evidence"][0]["text"] == "ก" * 5 + "…"
    assert "source_blocks" not in packet["evidence"][0]
    assert evidence[0]["text"] == "ก" * 20


def test_dimension_assessment_retries_incomplete_json(monkeypatch):
    calls = 0

    def fake_complete_json(settings, messages):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"assessments": []}
        return {
            "assessments": [
                {"criterion_id": "1.1.1", "score": 3, "confidence": 0.7, "evidence_sufficiency": "sufficient", "evidence_chunk_ids": [], "matched_points": [], "strengths": [], "missing_points": []},
            ]
        }

    monkeypatch.setattr("phase05.assessment.complete_json", fake_complete_json)
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)

    result = assess_dimension_with_llm(
        [{"criterion": {"criterion_id": "1.1.1"}, "evidence": []}],
        settings,
        max_attempts=2,
    )

    assert calls == 2
    assert result[0]["criterion_id"] == "1.1.1"


def test_dimension_error_shows_raw_response_shape(monkeypatch):
    monkeypatch.setattr(
        "phase05.assessment.complete_json",
        lambda settings, messages: {"criteria_assessment": []},
    )
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)

    try:
        assess_dimension_with_llm(
            [{"criterion": {"criterion_id": "1.1.1"}, "evidence": []}],
            settings,
        )
    except ValueError as error:
        assert '"criteria_assessment": []' in str(error)
    else:
        raise AssertionError("Expected invalid response shape to fail")


def test_evidence_sufficiency_is_optional_and_invalid_value_is_ignored(monkeypatch):
    monkeypatch.setattr(
        "phase05.assessment.complete_json",
        lambda settings, messages: {
            "assessments": [
                {
                    "criterion_id": "1.1.1",
                    "score": 3,
                    "confidence": 0.7,
                    "evidence_sufficiency": "partially_sufficient",
                    "evidence_chunk_ids": [],
                    "matched_points": [],
                    "strengths": [],
                    "missing_points": [],
                }
            ]
        },
    )
    settings = LLMSettings("anthropic", "claude-sonnet-5", None, "key", 512, True)

    result = assess_dimension_with_llm(
        [{"criterion": {"criterion_id": "1.1.1"}, "evidence": []}],
        settings,
    )

    assert result[0]["score"] == 3
    assert "evidence_sufficiency" not in result[0]


def test_checkpoint_can_resume_assessed_criteria(tmp_path):
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)
    phase04 = {"document_id": "DOC_TEST"}
    item = {
        "criterion": {"dimension_id": "D1", "criterion_id": "1.1.1"},
        "evidence": [],
        "assessment": {"score": 3},
        "status": "assessed",
    }
    output = _build_output(
        phase04,
        settings,
        [item],
        "2026-01-01T00:00:00+00:00",
        total_criteria=2,
        run_config={"batch_size": 2},
    )
    path = tmp_path / "phase05.json"

    _write_output_atomic(path, output)
    completed, created_at = _resume_items(path, phase04, settings)

    assert output["complete"] is False
    assert completed["1.1.1"] == item
    assert created_at == "2026-01-01T00:00:00+00:00"
    assert not path.with_suffix(".json.tmp").exists()


def _packet(criterion_id, evidence_count):
    return {
        "criterion": {"criterion_id": criterion_id},
        "evidence": [
            {"chunk_id": f"{criterion_id}-C{index}", "text": f"evidence {index}"}
            for index in range(1, evidence_count + 1)
        ],
    }


def _assessment(criterion_id, citation_ids):
    return {
        "criterion_id": criterion_id,
        "score": 3,
        "confidence": 0.8,
        "rationale_th": "มีหลักฐานสนับสนุน",
        "evidence_chunk_ids": citation_ids,
        "matched_points": [],
        "strengths": [],
        "missing_points": [],
    }


@pytest.mark.parametrize("top_k", [2, 5, 8])
def test_dynamic_citation_limit_follows_top_k(monkeypatch, top_k):
    packet = _packet("1.1.1", top_k)
    citation_ids = [item["chunk_id"] for item in packet["evidence"]]
    captured = {}

    def fake_complete_json(settings, messages):
        captured["messages"] = messages
        return {"assessments": [_assessment("1.1.1", citation_ids)]}

    monkeypatch.setattr("phase05.assessment.complete_json", fake_complete_json)
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)

    result = assess_dimension_with_llm([packet], settings, top_k=top_k)

    assert result[0]["citation_limit"] == top_k
    assert len(result[0]["evidence_chunk_ids"]) == top_k
    assert f"1.1.1={top_k}" in captured["messages"][0]["content"]
    user_prompt = json.loads(captured["messages"][1]["content"])
    assert user_prompt["criteria"][0]["citation_limit"] == top_k


def test_citation_limit_uses_actual_distinct_evidence_count(monkeypatch):
    packet = _packet("1.1.1", 3)
    citation_ids = [item["chunk_id"] for item in packet["evidence"]]
    monkeypatch.setattr(
        "phase05.assessment.complete_json",
        lambda settings, messages: {"assessments": [_assessment("1.1.1", citation_ids)]},
    )
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)

    result = assess_dimension_with_llm([packet], settings, top_k=5)

    assert result[0]["citation_limit"] == 3


def test_no_evidence_accepts_only_empty_citations(monkeypatch):
    packet = _packet("1.1.1", 0)
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)
    monkeypatch.setattr(
        "phase05.assessment.complete_json",
        lambda settings, messages: {"assessments": [_assessment("1.1.1", [])]},
    )
    assert assess_dimension_with_llm([packet], settings, top_k=5)[0]["citation_limit"] == 0

    monkeypatch.setattr(
        "phase05.assessment.complete_json",
        lambda settings, messages: {"assessments": [_assessment("1.1.1", ["MADE_UP"])]},
    )
    with pytest.raises(ValueError, match="not supplied.*1.1.1"):
        assess_dimension_with_llm([packet], settings, top_k=5)


def test_unknown_duplicate_and_too_many_citations_are_rejected(monkeypatch):
    packet = _packet("1.1.1", 5)
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)

    for citation_ids, error in [
        (["UNKNOWN"], "not supplied"),
        (["1.1.1-C1", "1.1.1-C1"], "duplicate"),
        (["1.1.1-C1", "1.1.1-C2", "1.1.1-C3"], "3 > 2"),
    ]:
        monkeypatch.setattr(
            "phase05.assessment.complete_json",
            lambda settings, messages, ids=citation_ids: {
                "assessments": [_assessment("1.1.1", ids)]
            },
        )
        with pytest.raises(ValueError, match=error):
            assess_dimension_with_llm([packet], settings, top_k=2)


def test_evidence_chunk_ids_must_be_json_array(monkeypatch):
    packet = _packet("1.1.1", 2)
    invalid = _assessment("1.1.1", [])
    invalid["evidence_chunk_ids"] = "1.1.1-C1"
    monkeypatch.setattr(
        "phase05.assessment.complete_json",
        lambda settings, messages: {"assessments": [invalid]},
    )
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)

    with pytest.raises(ValueError, match="invalid evidence_chunk_ids.*1.1.1"):
        assess_dimension_with_llm([packet], settings, top_k=2)


def test_citation_from_other_criterion_in_batch_is_rejected(monkeypatch):
    packets = [_packet("1.1.1", 2), _packet("1.1.2", 2)]
    monkeypatch.setattr(
        "phase05.assessment.complete_json",
        lambda settings, messages: {
            "assessments": [
                _assessment("1.1.1", ["1.1.2-C1"]),
                _assessment("1.1.2", []),
            ]
        },
    )
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)

    with pytest.raises(ValueError, match="not supplied.*1.1.1"):
        assess_dimension_with_llm(packets, settings, top_k=2)


def test_batch_size_does_not_change_per_criterion_limit(monkeypatch):
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)
    one_packet = [_packet("1.1.1", 5)]
    two_packets = [_packet("1.1.1", 5), _packet("1.1.2", 5)]

    def fake_complete_json(settings, messages):
        prompt = json.loads(messages[1]["content"])
        return {
            "assessments": [
                _assessment(
                    item["criterion"]["criterion_id"],
                    [evidence["chunk_id"] for evidence in item["evidence"]],
                )
                for item in prompt["criteria"]
            ]
        }

    monkeypatch.setattr("phase05.assessment.complete_json", fake_complete_json)

    single_result = assess_dimension_with_llm(one_packet, settings, top_k=5)
    batch_result = assess_dimension_with_llm(two_packets, settings, top_k=5)

    assert single_result[0]["citation_limit"] == 5
    assert [item["citation_limit"] for item in batch_result] == [5, 5]


def test_single_assessment_uses_same_dynamic_citation_rule(monkeypatch):
    packet = _packet("1.1.1", 3)
    citation_ids = [item["chunk_id"] for item in packet["evidence"]]
    captured = {}

    def fake_complete_json(settings, messages):
        captured["messages"] = messages
        result = _assessment("1.1.1", citation_ids)
        result.pop("criterion_id")
        return result

    monkeypatch.setattr("phase05.assessment.complete_json", fake_complete_json)
    settings = LLMSettings("ollama", "qwen3:8b", "http://localhost:11434/v1", None, 512, True)

    result = assess_with_llm(packet, settings, top_k=5)

    assert result["citation_limit"] == 3
    assert "at most 3 distinct IDs" in captured["messages"][0]["content"]
