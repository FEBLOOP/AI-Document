from llm.config import LLMSettings
from phase05.assessment import assess_dimension_with_llm
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
