from __future__ import annotations
import json
from collections.abc import Callable
from typing import Any

from llm import LLMSettings, complete_json
from .rubric import DEFAULT_SCORING_GUIDE


class AssessmentValidationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        criterion_id: str | None = None,
        invalid_ids: list[str] | None = None,
        allowed_ids: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.criterion_id = criterion_id
        self.invalid_ids = invalid_ids or []
        self.allowed_ids = allowed_ids or []


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

    raw_ids = assessment["evidence_chunk_ids"]
    if any(not isinstance(item, str) or not item.strip() for item in raw_ids):
        raise ValueError(
            f"LLM returned non-string or empty evidence_chunk_ids for criterion {criterion_id}."
        )

    # Deduplicate while preserving order
    citation_ids = list(dict.fromkeys(item.strip() for item in raw_ids))

    # Drop unsupplied or cross-criterion chunk IDs with a warning instead of aborting
    supplied_ids = set(_supplied_evidence_ids(packet))
    unknown_ids = [cid for cid in citation_ids if cid not in supplied_ids]
    if unknown_ids:
        print(
            f"[WARN] Dropping unsupplied evidence_chunk_ids for criterion {criterion_id}: {unknown_ids}",
            flush=True,
        )
        citation_ids = [cid for cid in citation_ids if cid in supplied_ids]

    allowed_citations = _citation_limit(packet, top_k)
    if len(citation_ids) > allowed_citations:
        citation_ids = citation_ids[:allowed_citations]

    assessment["evidence_chunk_ids"] = citation_ids
    assessment["citation_limit"] = allowed_citations

    strengths = assessment["strengths"]
    if any(not isinstance(item, str) or not item.strip() for item in strengths):
        raise ValueError(
            f"LLM returned non-string or empty strengths for criterion {criterion_id}."
        )
    if len(strengths) != len(set(strengths)):
        raise ValueError(
            f"LLM returned duplicate strengths for criterion {criterion_id}."
        )

    mapping = assessment.get("strength_evidence_mapping")
    if not isinstance(mapping, list):
        raise ValueError(
            f"LLM returned invalid strength_evidence_mapping for criterion {criterion_id}."
        )
    selected_ids = set(citation_ids)
    combined = {strength: [] for strength in strengths}
    mapped_strengths: set[str] = set()
    for entry in mapping:
        if not isinstance(entry, dict):
            raise ValueError(
                f"LLM returned a non-object strength_evidence_mapping entry for criterion {criterion_id}."
            )
        strength = entry.get("strength")
        if not isinstance(strength, str) or strength not in combined:
            raise ValueError(
                f"LLM returned unknown strength in strength_evidence_mapping for criterion {criterion_id}: "
                f"{strength!r}."
            )
        chunk_ids = entry.get("evidence_chunk_ids")
        if not isinstance(chunk_ids, list):
            raise ValueError(
                f"LLM returned invalid mapped evidence_chunk_ids for criterion {criterion_id}."
            )
        mapped_strengths.add(strength)
        for chunk_id in chunk_ids:
            if not isinstance(chunk_id, str) or not chunk_id.strip():
                raise ValueError(
                    f"LLM returned an invalid mapped chunk ID for criterion {criterion_id}."
                )
            if chunk_id not in selected_ids:
                print(
                    f"[WARN] Dropping unselected mapped chunk ID for criterion {criterion_id}: {chunk_id}",
                    flush=True,
                )
                continue
            if chunk_id not in combined[strength]:
                combined[strength].append(chunk_id)
    missing_strengths = [strength for strength in strengths if strength not in mapped_strengths]
    if missing_strengths:
        raise ValueError(
            f"LLM omitted strengths from strength_evidence_mapping for criterion "
            f"{criterion_id}: {missing_strengths}."
        )
    assessment["strength_evidence_mapping"] = [
        {"strength": strength, "evidence_chunk_ids": combined[strength]}
        for strength in strengths
    ]
    return assessment


def _corrective_feedback(
    error: ValueError,
    packets: list[dict[str, Any]],
) -> dict[str, Any]:
    allowed_by_criterion = {
        str(packet.get("criterion", {}).get("criterion_id", "")): _supplied_evidence_ids(packet)
        for packet in packets
    }
    feedback: dict[str, Any] = {
        "previous_response_error": str(error),
        "required_action": (
            "Return a completely corrected JSON response. Use only each criterion's "
            "allowed_evidence_chunk_ids. Never reuse an ID from another criterion."
        ),
        "allowed_evidence_chunk_ids_by_criterion": allowed_by_criterion,
    }
    if isinstance(error, AssessmentValidationError):
        feedback.update(
            {
                "invalid_criterion_id": error.criterion_id,
                "invalid_evidence_chunk_ids": error.invalid_ids,
                "allowed_evidence_chunk_ids": error.allowed_ids,
            }
        )
    return feedback


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
                "source_id": item.get("source_id"),
                "source_filename": item.get("source_filename"),
            }
        )
    return {
        "criterion": criterion,
        "evidence": compact_evidence,
        "instructions": (
            "ใช้เฉพาะหลักฐานที่ให้มา ห้ามแต่งข้อมูลหรืออ้างหน้า/บล็อกที่ไม่มีใน evidence. "
            "คะแนน 1-5 ตาม scoring_guide และต้องระบุ evidence_chunk_ids, matched_points, "
            "strengths, strength_evidence_mapping และ missing_points; evidence_sufficiency "
            "เป็นข้อมูลเสริม. แต่ละ strength_evidence_mapping ต้องใช้ข้อความ strength เดิมทุกตัวอักษร "
            "และอ้างได้เฉพาะ evidence_chunk_ids ที่เลือกไว้แล้ว; ถ้าไม่มีหลักฐานสนับสนุนโดยตรงให้ใช้รายการว่าง."
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
        "allowed_evidence_chunk_ids": _supplied_evidence_ids(packet),
    }
    prompt = json.dumps(
        {"scoring_guide": _guide(scoring_guide), **prompt_packet},
        ensure_ascii=False,
    )
    assessment = complete_json(settings, [
        {"role": "system", "content": f"You are a careful Thai IOP assessor. Evaluate actual implementation, organizational coverage, continuity, systematic practice, sustainability, and role-model evidence according to the supplied scoring_guide. Evidence count alone must never determine a score. Distinguish insufficient evidence from evidence that confirms no implementation. Score 5 requires explicit evidence of systematic, sustainable, role-model practice. Return only one compact, valid JSON object with score (integer 1-5), confidence (0-1), rationale_th, evidence_chunk_ids, matched_points, strengths, strength_evidence_mapping, missing_points. strength_evidence_mapping must contain exactly one entry for every strength, use the strength text verbatim, and contain only selected evidence_chunk_ids that directly support that specific strength; use an empty ID list when no selected evidence directly supports it. Many-to-many links are allowed. Never infer links by position. evidence_chunk_ids may contain at most {allowed_citations} distinct IDs from the supplied evidence. Select only directly relevant evidence; do not add IDs merely to reach the limit and never invent IDs. You may optionally include evidence_sufficiency as sufficient or insufficient when it can be determined. Use only supplied evidence and never invent facts. No Markdown and no text outside JSON."},
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
    corrective_feedback: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _assess_dimension_once(
                packets,
                settings,
                _guide(scoring_guide),
                top_k,
                corrective_feedback,
            )
        except ValueError as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            corrective_feedback = _corrective_feedback(exc, packets)
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
    corrective_feedback: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    prompt_packets = [
        {
            **packet,
            "citation_limit": _citation_limit(packet, top_k),
            "allowed_evidence_chunk_ids": _supplied_evidence_ids(packet),
        }
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
            "corrective_feedback": corrective_feedback,
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
                    "to the supplied scoring_guide. "
                    "Evidence count alone must never determine a score. "
                    "Distinguish insufficient evidence from evidence that confirms "
                    "no implementation. "
                    "Score 5 requires explicit evidence of systematic, "
                    "sustainable, role-model practice. "

                    "Return only one compact valid JSON object in this format: "
                    '{"assessments":[{"criterion_id":"...",'
                    '"score":1,"confidence":0.0,"rationale_th":"...",'
                    '"evidence_chunk_ids":[],"matched_points":[],'
                    '"strengths":[],"strength_evidence_mapping":[],'
                    '"missing_points":[]}]} '

                    "Return exactly one assessment for every supplied criterion_id. "
                    "score must be integer 1-5 and confidence must be 0-1. "
                    "Use only supplied evidence. Never invent evidence. "
                    "rationale_th must be one short Thai sentence. "

                    f"Requested top_k is {requested_top_k}. "
                    f"Citation limits per criterion are: {limit_summary}. "

                    "evidence_chunk_ids may contain at most the citation_limit "
                    "distinct IDs from the evidence supplied for that criterion. "
                    "Select only evidence chunks that directly support the assessment. "
                    "Do not include irrelevant chunks just to reach the maximum. "

                    "CRITICAL EVIDENCE CITATION RULES: "
                    "Each criterion has its own independent evidence list. "
                    "The allowed_evidence_chunk_ids field is the authoritative "
                    "whitelist for that criterion. "
                    "For each criterion_id, evidence_chunk_ids MUST contain only "
                    "chunk_id values explicitly present in that criterion's own "
                    "evidence list. "
                    "Treat each criterion's evidence list as a strict whitelist. "
                    "Never invent, modify, guess, or fabricate chunk IDs. "
                    "Never copy chunk IDs from another criterion unless those IDs "
                    "are also explicitly present in the current criterion's evidence list. "
                    "A chunk ID appearing elsewhere in the batch does not make it "
                    "valid for the current criterion. "
                    "Do not select evidence_chunk_ids based on memory or similarity "
                    "to other chunk IDs. "
                    "Before returning the final JSON, verify that every "
                    "evidence_chunk_id belongs to the corresponding criterion_id's "
                    "supplied evidence list. "
                    "If a chunk ID is not present in that criterion's evidence list, "
                    "do not include it in evidence_chunk_ids. "
                    "If no supplied evidence directly supports the assessment, "
                    "return an empty evidence_chunk_ids list for that criterion. "
                    "Never use an empty citation list to conceal relevant evidence "
                    "that is actually supplied. "
                    "When corrective_feedback is present, the previous response was "
                    "rejected. Correct every reported problem and use the invalid and "
                    "allowed ID lists in that feedback; do not repeat invalid IDs. "

                    "EVIDENCE AND SCORING CONSISTENCY: "
                    "Scores, rationale_th, matched_points, strengths, and missing_points "
                    "must be consistent with the evidence supplied for each criterion. "
                    "Do not assume that evidence supplied for one criterion "
                    "also supports another criterion. "
                    "Do not award a high score merely because many chunks were retrieved. "
                    "If evidence is incomplete, clearly describe the missing information "
                    "without inventing facts. "

                    "strengths and missing_points must each contain at most "
                    "2 short phrases. "
                    "strength_evidence_mapping MUST contain one entry for every "
                    "strength and use that strength text verbatim. Each entry's "
                    "evidence_chunk_ids may contain only IDs already selected in "
                    "that same assessment's evidence_chunk_ids and only when those "
                    "chunks directly support that specific strength. Use an empty "
                    "list when direct support is absent. Many-to-many links are "
                    "allowed. Never infer a link from array position, similarity, "
                    "or another criterion. "
                    "evidence_sufficiency is optional; when included it must be "
                    "sufficient or insufficient. "

                    "Return all requested criterion assessments in one JSON object. "
                    "Ensure that the JSON is complete, syntactically valid, "
                    "and contains no duplicate criterion_id values. "
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
