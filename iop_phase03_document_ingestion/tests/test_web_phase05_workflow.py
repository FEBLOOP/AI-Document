from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from llm import LLMSettings
from phase04.config import Settings
from phase05 import assessment as assessment_module
from phase05.assessment import assess_dimension_with_llm
from phase05.cli import _resume_items
from phase05 import cli as cli_module
from phase05.retrieval import query_embedder_from_chunks, retrieve
import webapp.main as web_main


LLM_SETTINGS = LLMSettings("openai", "test-model", None, "test", 512, False)


def _packet(criterion_id: str = "1.3.1") -> dict:
    return {
        "criterion": {"criterion_id": criterion_id},
        "evidence": [
            {"chunk_id": "C1", "text": "นโยบายนวัตกรรม"},
            {"chunk_id": "C2", "text": "คณะทำงานนวัตกรรม"},
        ],
    }


def _valid_assessment(criterion_id: str = "1.3.1") -> dict:
    return {
        "criterion_id": criterion_id,
        "score": 3,
        "confidence": 0.8,
        "rationale_th": "มีหลักฐานสนับสนุนบางส่วน",
        "evidence_chunk_ids": ["C1"],
        "matched_points": [],
        "strengths": ["มีนโยบาย"],
        "strength_evidence_mapping": [
            {"strength": "มีนโยบาย", "evidence_chunk_ids": ["C1"]}
        ],
        "missing_points": [],
    }


def test_web_phase05_command_uses_requested_defaults_and_resume(tmp_path: Path) -> None:
    command = web_main._phase05_command_arguments(
        tmp_path / "phase04.json",
        tmp_path / "phase05.json",
        "openai",
        "gpt-test",
        ["D1"],
    )

    def value(flag: str) -> str:
        return command[command.index(flag) + 1]

    assert value("--top-k") == "5"
    assert value("--semantic-weight") == "0.7"
    assert value("--batch-size") == "1"
    assert value("--max-evidence-chars") == "1200"
    assert value("--retrieval") == "hybrid"
    assert value("--retrieval-fallback") == "error"
    assert "--resume" in command


def test_web_hybrid_defaults_to_sentence_transformers_and_explicit_none_errors(monkeypatch) -> None:
    monkeypatch.setattr(web_main, "WEB_RETRIEVAL_MODE", "hybrid")
    monkeypatch.delenv("PHASE04_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setattr(
        web_main.Settings,
        "load",
        classmethod(lambda cls: Settings(embedding_provider="none")),
    )

    settings = web_main._web_phase04_settings()

    assert settings.embedding_provider == "sentence_transformers"
    assert "paraphrase-multilingual" in settings.embedding_model

    monkeypatch.setenv("PHASE04_EMBEDDING_PROVIDER", "none")
    with pytest.raises(RuntimeError, match="requires Phase 04 embeddings"):
        web_main._web_phase04_settings()


def test_web_allows_only_explicitly_configured_lexical_fallback(monkeypatch) -> None:
    monkeypatch.setattr(web_main, "WEB_RETRIEVAL_MODE", "hybrid")
    monkeypatch.setattr(web_main, "WEB_RETRIEVAL_FALLBACK", "lexical")
    monkeypatch.setenv("PHASE04_EMBEDDING_PROVIDER", "none")
    monkeypatch.setattr(
        web_main.Settings,
        "load",
        classmethod(lambda cls: Settings(embedding_provider="none")),
    )

    settings = web_main._web_phase04_settings()

    assert settings.embedding_provider == "none"


def test_hybrid_uses_both_scores_and_rejects_missing_or_partial_embeddings() -> None:
    chunks = [
        {
            "chunk_id": "C1",
            "normalized_text": "innovation policy governance",
            "embedding": {"provider": "hash", "model": "hash", "vector": [1.0, 0.0]},
        },
        {
            "chunk_id": "C2",
            "normalized_text": "unrelated operations",
            "embedding": {"provider": "hash", "model": "hash", "vector": [0.0, 1.0]},
        },
    ]
    hits = retrieve(
        chunks,
        "innovation policy",
        2,
        mode="hybrid",
        query_vector=[1.0, 0.0],
        semantic_weight=0.7,
    )
    assert hits[0]["chunk"]["chunk_id"] == "C1"
    assert hits[0]["lexical_score"] > 0
    assert hits[0]["semantic_score"] == 1.0

    missing = [{"chunk_id": "C1", "normalized_text": "innovation"}]
    with pytest.raises(ValueError, match="requires embeddings for every chunk"):
        retrieve(
            missing,
            "innovation",
            1,
            mode="hybrid",
            query_vector=[1.0],
        )
    partial = [chunks[0], missing[0]]
    with pytest.raises(ValueError, match="partial set of embeddings"):
        query_embedder_from_chunks(partial)


def test_invalid_evidence_id_retry_contains_targeted_corrective_feedback(monkeypatch) -> None:
    invalid = _valid_assessment()
    invalid["evidence_chunk_ids"] = ["DOC_WRONG_C0205"]
    invalid["strength_evidence_mapping"] = [
        {"strength": "มีนโยบาย", "evidence_chunk_ids": ["DOC_WRONG_C0205"]}
    ]
    responses = iter([
        {"assessments": [invalid]},
        {"assessments": [_valid_assessment()]},
    ])
    prompts: list[list[dict]] = []

    def fake_complete(_settings, messages):
        prompts.append(messages)
        return next(responses)

    monkeypatch.setattr(assessment_module, "complete_json", fake_complete)

    result = assess_dimension_with_llm(
        [_packet()],
        LLM_SETTINGS,
        max_attempts=2,
        top_k=2,
    )

    assert result[0]["evidence_chunk_ids"] == ["C1"]
    assert len(prompts) == 2
    first_payload = json.loads(prompts[0][1]["content"])
    assert first_payload["criteria"][0]["allowed_evidence_chunk_ids"] == ["C1", "C2"]
    retry_payload = json.loads(prompts[1][1]["content"])
    feedback = retry_payload["corrective_feedback"]
    assert feedback["invalid_criterion_id"] == "1.3.1"
    assert feedback["invalid_evidence_chunk_ids"] == ["DOC_WRONG_C0205"]
    assert feedback["allowed_evidence_chunk_ids"] == ["C1", "C2"]


def test_resume_rejects_duplicate_results_and_changed_configuration(tmp_path: Path) -> None:
    item = {
        "criterion": {"criterion_id": "1.1.1"},
        "assessment": {"score": 3},
        "status": "assessed",
    }
    path = tmp_path / "phase05.json"
    payload = {
        "document_id": "DOC_TEST",
        "assessment_provider": "openai",
        "assessment_model": "test-model",
        "run_config": {"top_k": 5, "batch_size": 1},
        "dimensions": [{"dimension_id": "D1", "criteria": [item, item]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate criterion_id"):
        _resume_items(
            path,
            {"document_id": "DOC_TEST"},
            LLM_SETTINGS,
            {"top_k": 5, "batch_size": 1},
        )

    payload["dimensions"][0]["criteria"] = [item]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="different run configuration"):
        _resume_items(
            path,
            {"document_id": "DOC_TEST"},
            LLM_SETTINGS,
            {"top_k": 2, "batch_size": 1},
        )


def test_web_subprocess_restarts_from_existing_checkpoint(tmp_path: Path, monkeypatch) -> None:
    phase05_path = tmp_path / "phase05.json"
    phase05_path.write_text('{"complete": false}', encoding="utf-8")
    return_codes = iter([1, 0])
    calls = []

    class FakeProcess:
        def __init__(self, command, **_kwargs):
            calls.append(command)
            self.stdout = iter(["Retrieval: requested=hybrid, effective=hybrid\n"])
            self.return_code = next(return_codes)

        def wait(self):
            return self.return_code

    monkeypatch.setattr(web_main.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(web_main, "WEB_PHASE05_RESTARTS", 1)
    with web_main.JOBS_LOCK:
        web_main.JOBS["job"] = {
            "id": "job",
            "status": "running",
            "progress": 50,
            "paths": {},
        }

    command = ["python", "-m", "phase05.cli", "--resume"]
    web_main._run_phase05_subprocess(
        "job", command, phase05_path, "OpenAI API", 55, 35
    )

    assert len(calls) == 2
    assert all("--resume" in call for call in calls)
    assert web_main.JOBS["job"]["retrieval_effective"] == "hybrid"


def test_cli_keeps_explicit_legacy_fallback_and_supports_strict_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    phase04_path = tmp_path / "phase04.json"
    rubric_path = tmp_path / "rubric.json"
    output_path = tmp_path / "phase05.json"
    phase04_path.write_text(json.dumps({
        "document_id": "DOC_TEST",
        "chunks": [{
            "chunk_id": "DOC_TEST_C0001",
            "normalized_text": "innovation policy",
            "page_start": 1,
            "page_end": 1,
        }],
    }), encoding="utf-8")
    rubric_path.write_text(json.dumps({
        "scoring_guide": {str(index): f"level {index}" for index in range(1, 6)},
        "dimensions": [{
            "id": "D1",
            "name_th": "D1",
            "components": [{
                "id": "1.1",
                "name_th": "component",
                "criteria": [{
                    "id": "1.1.1",
                    "description": "innovation policy",
                    "assessment_points": [],
                    "supporting_documents": [],
                }],
            }],
        }],
    }), encoding="utf-8")
    base_args = [
        "phase05.cli",
        "--phase04", str(phase04_path),
        "--rubric", str(rubric_path),
        "--output", str(output_path),
        "--provider", "none",
    ]
    monkeypatch.setattr(sys, "argv", base_args)
    cli_module.main()
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["run_config"]["retrieval"] == "hybrid"
    assert output["run_config"]["effective_retrieval"] == "lexical"
    assert "Hybrid retrieval fallback to lexical" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", base_args + ["--retrieval-fallback", "error"])
    with pytest.raises(SystemExit):
        cli_module.main()
    assert "Embedding retrieval is unavailable" in capsys.readouterr().err
