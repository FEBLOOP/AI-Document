from __future__ import annotations
import json
from collections.abc import Callable
from typing import Any

from llm import LLMSettings, complete_json
from .rubric import DEFAULT_SCORING_GUIDE


def _guide(scoring_guide: dict[str, str] | None) -> dict[str, str]:
    return scoring_guide or DEFAULT_SCORING_GUIDE


def _supplied_evidence_ids(packet: dict[str, Any]) -> list[str]:
    """Return distinct, non-empty evidence IDs in their supplied order."""
    result: list[str] = []
    for item in packet.get("evidence", []):
        chunk_id = item.get("chunk_id")
        if chunk_id is None:
            continue
        normalized = str(chunk_id).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _citation_limit(packet: dict[str, Any], top_k: int | None) -> int:
    supplied_count = len(_supplied_evidence_ids(packet))
    requested = supplied_count if top_k is None else top_k
    if requested < 0:
        raise ValueError("top_k must be zero or greater for citation validation.")
    return min(requested, supplied_count)


def _validate_assessment_fields(
    assessment: dict[str, Any],
    packet: dict[str, Any],
    top_k: int | None,
    criterion_id: str,
) -> dict[str, Any]:
    score = assessment.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
        raise ValueError(f"LLM returned invalid score for criterion {criterion_id}.")

    assessment["confidence"] = max(
        0, min(1, float(assessment.get("confidence", 0)))
    )
    sufficiency = str(assessment.get("evidence_sufficiency", "")).lower()
    if sufficiency in {"sufficient", "insufficient"}:
        assessment["evidence_sufficiency"] = sufficiency
    else:
        assessment.pop("evidence_sufficiency", None)

    for field in ("evidence_chunk_ids", "matched_points", "strengths", "missing_points"):
        if not isinstance(assessment.get(field), list):
            raise ValueError(
                f"LLM returned invalid {field} for criterion {criterion_id}."
            )

    citation_ids = assessment["evidence_chunk_ids"]
    if any(not isinstance(item, str) or not item.strip() for item in citation_ids):
        raise ValueError(
            f"LLM returned non-string or empty evidence_chunk_ids for criterion {criterion_id}."
        )
    if len(citation_ids) != len(set(citation_ids)):
        raise ValueError(
            f"LLM returned duplicate evidence_chunk_ids for criterion {criterion_id}: "
            f"{len(citation_ids)} IDs are not distinct."
        )

    supplied_ids = set(_supplied_evidence_ids(packet))
    unknown_ids = set(citation_ids) - supplied_ids
    if unknown_ids:
        raise ValueError(
            f"LLM returned evidence_chunk_ids not supplied for criterion {criterion_id}: "
            f"{sorted(unknown_ids)}."
        )

    allowed_citations = _citation_limit(packet, top_k)
    if len(citation_ids) > allowed_citations:
        raise ValueError(
            f"Too many evidence_chunk_ids for criterion {criterion_id}: "
            f"{len(citation_ids)} > {allowed_citations}."
        )
    assessment["citation_limit"] = allowed_citations
    return assessment


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
            "คะแนน 1-5 ตาม scoring_guide และต้องระบุ evidence_chunk_ids, matched_points, "
            "strengths และ missing_points; evidence_sufficiency เป็นข้อมูลเสริม."
        ),
    }


def assess_with_llm(
    packet: dict[str, Any],
    settings: LLMSettings,
    scoring_guide: dict[str, str] | None = None,
    top_k: int | None = None,
) -> dict[str, Any]:
    allowed_citations = _citation_limit(packet, top_k)
    prompt_packet = {
        **packet,
        "citation_limit": allowed_citations,
    }
    prompt = json.dumps(
        {"scoring_guide": _guide(scoring_guide), **prompt_packet},
        ensure_ascii=False,
    )
    assessment = complete_json(settings, [
        {"role": "system", "content": f"You are a careful Thai IOP assessor. Evaluate actual implementation, organizational coverage, continuity, systematic practice, sustainability, and role-model evidence according to the supplied scoring_guide. Evidence count alone must never determine a score. Distinguish insufficient evidence from evidence that confirms no implementation. Score 5 requires explicit evidence of systematic, sustainable, role-model practice. Return only one compact, valid JSON object with score (integer 1-5), confidence (0-1), rationale_th, evidence_chunk_ids, matched_points, strengths, missing_points. evidence_chunk_ids may contain at most {allowed_citations} distinct IDs from the supplied evidence. Select only directly relevant evidence; do not add IDs merely to reach the limit and never invent IDs. You may optionally include evidence_sufficiency as sufficient or insufficient when it can be determined. Use only supplied evidence and never invent facts. No Markdown and no text outside JSON."},
        {"role": "user", "content": prompt},
    ])
    criterion_id = str(packet.get("criterion", {}).get("criterion_id", "<single>"))
    return _validate_assessment_fields(assessment, packet, top_k, criterion_id)


def assess_dimension_with_llm(
    packets: list[dict[str, Any]],
    settings: LLMSettings,
    max_attempts: int = 1,
    on_retry: Callable[[int, Exception], None] | None = None,
    scoring_guide: dict[str, str] | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Assess a mini-batch and retry malformed or incomplete JSON responses."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1.")

    last_error: ValueError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _assess_dimension_once(
                packets,
                settings,
                _guide(scoring_guide),
                top_k,
            )
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
    scoring_guide: dict[str, str],
    top_k: int | None,
) -> list[dict[str, Any]]:
    prompt_packets = [
        {**packet, "citation_limit": _citation_limit(packet, top_k)}
        for packet in packets
    ]
    requested_top_k = (
        max((_citation_limit(packet, None) for packet in packets), default=0)
        if top_k is None
        else top_k
    )
    limit_summary = ", ".join(
        f"{packet['criterion']['criterion_id']}={packet['citation_limit']}"
        for packet in prompt_packets
    )
    prompt = json.dumps(
        {
            "scoring_guide": scoring_guide,
            "requested_top_k": requested_top_k,
            "criteria": prompt_packets,
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
                    "Evaluate actual implementation, organizational coverage, continuity, "
                    "systematic practice, sustainability, and role-model evidence according "
                    "to the supplied scoring_guide. Evidence count alone must never determine "
                    "a score. Distinguish insufficient evidence from evidence that confirms "
                    "no implementation. Score 5 requires explicit evidence of systematic, "
                    "sustainable, role-model practice. "
                    "Return only one compact valid JSON object in this format: "
                    '{"assessments":[{"criterion_id":"...",'
                    '"score":1,"confidence":0.0,"rationale_th":"...",'
                    '"evidence_chunk_ids":[],"matched_points":[],'
                    '"strengths":[],"missing_points":[]}]} '
                    "Return exactly one assessment for every supplied criterion_id. "
                    "score must be integer 1-5 and confidence must be 0-1. "
                    "Use only supplied evidence. Never invent evidence. "
                    "rationale_th must be one short Thai sentence. "
                    f"Requested top_k is {requested_top_k}. Citation limits per criterion are: "
                    f"{limit_summary}. evidence_chunk_ids may contain at most the citation_limit "
                    "distinct IDs from the evidence supplied for that criterion. Select only "
                    "evidence chunks that directly support the assessment. Do not include "
                    "irrelevant chunks just to reach the maximum. Never invent chunk IDs or "
                    "use evidence supplied for another criterion. "
                    "strengths and missing_points must each contain at most 2 short phrases. "
                    "evidence_sufficiency is optional; when included it must be sufficient or insufficient. "
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

        packet = next(
            packet
            for packet in packets
            if str(packet["criterion"]["criterion_id"]) == criterion_id
        )
        by_id[criterion_id] = _validate_assessment_fields(
            assessment,
            packet,
            top_k,
            criterion_id,
        )

    ordered = []
    for packet in packets:
        criterion_id = str(packet["criterion"]["criterion_id"])
        if criterion_id not in by_id:
            raise ValueError(
                f"LLM did not return assessment for criterion {criterion_id}."
            )
        ordered.append(by_id[criterion_id])

    return ordered
