from __future__ import annotations
import json
from collections.abc import Callable
from typing import Any

from llm import LLMSettings, complete_json


SCORING_GUIDE = {"1": "แทบไม่มีหลักฐาน", "2": "มีบางส่วน", "3": "มีส่วนใหญ่", "4": "ครบถ้วน", "5": "ครบถ้วนและเป็นต้นแบบ"}


def review_packet(
    criterion: dict[str, Any],
    evidence: list[dict[str, Any]],
    max_evidence_chars: int = 800,
) -> dict[str, Any]:
    """Build a compact prompt packet while keeping full evidence in the output."""
    compact_evidence = []
    for item in evidence:
        text = str(item.get("text", ""))
        if len(text) > max_evidence_chars:
            text = text[:max_evidence_chars].rstrip() + "…"
        compact_evidence.append(
            {
                "chunk_id": item.get("chunk_id"),
                "relevance_score": item.get("relevance_score"),
                "text": text,
                "section": item.get("section"),
                "page_start": item.get("page_start"),
                "page_end": item.get("page_end"),
            }
        )
    return {
        "criterion": criterion,
        "evidence": compact_evidence,
        "instructions": (
            "ใช้เฉพาะหลักฐานที่ให้มา ห้ามแต่งข้อมูลหรืออ้างหน้า/บล็อกที่ไม่มีใน evidence. "
            "คะแนน 1-5 ตาม scoring_guide และต้องระบุ evidence_chunk_ids, strengths, missing_points."
        ),
    }


def assess_with_llm(packet: dict[str, Any], settings: LLMSettings) -> dict[str, Any]:
    prompt = json.dumps({"scoring_guide": SCORING_GUIDE, **packet}, ensure_ascii=False)
    assessment = complete_json(settings, [
        {"role": "system", "content": "You are a careful Thai IOP assessor. Return only one compact, valid JSON object with score (integer 1-5), confidence (0-1), rationale_th, evidence_chunk_ids, strengths, missing_points. No Markdown and no text outside JSON. Keep rationale_th to one Thai sentence of at most 180 characters. evidence_chunk_ids must contain at most 2 supplied IDs. strengths and missing_points must each contain at most 2 short phrases of at most 80 characters. Do not repeat the supplied evidence."},
        {"role": "user", "content": prompt},
    ])
    if not isinstance(assessment.get("score"), int) or not 1 <= assessment["score"] <= 5:
        raise ValueError("LLM returned an invalid score.")
    assessment["confidence"] = max(0, min(1, float(assessment.get("confidence", 0))))
    return assessment


def assess_dimension_with_llm(
    packets: list[dict[str, Any]],
    settings: LLMSettings,
    max_attempts: int = 1,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> list[dict[str, Any]]:
    """Assess a mini-batch and retry malformed or incomplete JSON responses."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")

    last_error: ValueError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _assess_dimension_once(packets, settings)
        except ValueError as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            if on_retry:
                on_retry(attempt + 1, exc)

    raise ValueError(
        f"LLM returned invalid assessment JSON after {max_attempts} attempts: "
        f"{last_error}"
    ) from last_error


def _assess_dimension_once(
    packets: list[dict[str, Any]],
    settings: LLMSettings,
) -> list[dict[str, Any]]:
    prompt = json.dumps(
        {
            "scoring_guide": SCORING_GUIDE,
            "criteria": packets,
        },
        ensure_ascii=False,
    )

    result = complete_json(
        settings,
        [
            {
                "role": "system",
                "content": (
                    "You are a careful Thai IOP assessor. "
                    "Assess every criterion supplied in criteria. "
                    "Return only one compact valid JSON object in this format: "
                    '{"assessments":[{"criterion_id":"...",'
                    '"score":1,"confidence":0.0,"rationale_th":"...",'
                    '"evidence_chunk_ids":[],"strengths":[],"missing_points":[]}]} '
                    "Return exactly one assessment for every supplied criterion_id. "
                    "score must be integer 1-5 and confidence must be 0-1. "
                    "Use only supplied evidence. Never invent evidence. "
                    "rationale_th must be one short Thai sentence. "
                    "evidence_chunk_ids must contain at most 2 supplied IDs. "
                    "strengths and missing_points must each contain at most 2 short phrases. "
                    "No Markdown and no text outside JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )

    assessments = result.get("assessments")
    if not isinstance(assessments, list):
        preview = json.dumps(result, ensure_ascii=False)[:2000]
        raise ValueError(
            "LLM did not return an assessments list. "
            f"Raw response: {preview}"
        )

    expected_ids = {
        str(packet["criterion"]["criterion_id"])
        for packet in packets
    }
    by_id: dict[str, dict[str, Any]] = {}

    for assessment in assessments:
        if not isinstance(assessment, dict):
            raise ValueError("LLM returned a non-object assessment.")
        criterion_id = str(assessment.get("criterion_id", ""))
        if criterion_id not in expected_ids:
            raise ValueError(
                f"LLM returned an unknown criterion_id: {criterion_id or '<missing>'}."
            )
        if criterion_id in by_id:
            raise ValueError(
                f"LLM returned duplicate assessment for criterion {criterion_id}."
            )

        score = assessment.get("score")
        if not isinstance(score, int) or not 1 <= score <= 5:
            raise ValueError(
                f"LLM returned invalid score for criterion {criterion_id}."
            )

        assessment["confidence"] = max(
            0, min(1, float(assessment.get("confidence", 0)))
        )
        by_id[criterion_id] = assessment

    ordered = []
    for packet in packets:
        criterion_id = str(packet["criterion"]["criterion_id"])
        if criterion_id not in by_id:
            raise ValueError(
                f"LLM did not return assessment for criterion {criterion_id}."
            )
        ordered.append(by_id[criterion_id])

    return ordered
