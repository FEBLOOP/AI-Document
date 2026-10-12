from llm.config import LLMSettings
from phase05.assessment import assess_dimension_with_llm, review_packet
from phase05.cli import _build_output, _resume_items, _write_output_atomic
from phase05.retrieval import retrieve
from phase05.rubric import load_criteria


def test_retrieval_returns_matching_chunk():
    hits = retrieve([{"chunk_id": "C1", "normalized_text": "นโยบายด้านนวัตกรรมและผู้บริหาร"}, {"chunk_id": "C2", "normalized_text": "การเงินบัญชี"}], "ผู้บริหาร นวัตกรรม", 1)
    assert hits[0]["chunk"]["chunk_id"] == "C1"


def test_iop_hierarchy_is_flattened():
    rubric = {"dimensions": [{"id": "D1", "components": [{"id": "1.1", "criteria": [{"id": "1.1.1", "description": "x"}]}]}]}
    assert load_criteria(rubric)[0]["criterion_id"] == "1.1.1"


def test_dimension_assessment_uses_one_call_and_preserves_criterion_order(monkeypatch):
    calls = []

    def fake_complete_json(settings, messages):
        calls.append((settings, messages))
        return {
            "assessments": [
                {"criterion_id": "1.1.2", "score": 4, "confidence": 0.8},
                {"criterion_id": "1.1.1", "score": 3, "confidence": 0.7},
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
                {"criterion_id": "1.1.1", "score": 3, "confidence": 0.7},
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
