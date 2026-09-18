from __future__ import annotations

import copy
import json
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any

from llm import LLMSettings, complete_json, load_llm_settings

from . import SCHEMA_VERSION
from .styles import load_good_example_styles
from .validation import assessment_id, citation_map, snapshot_digest, source_snapshot, validate_summary_integrity


def _items(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _dedupe(items: list[str]) -> list[str]:
    return list(OrderedDict.fromkeys(item for item in items if item.strip()))


def _narrative(result: dict[str, Any]) -> str:
    assessment = result.get("assessment")
    criterion = result.get("criterion", {})
    criterion_id = criterion.get("criterion_id", "")
    if not assessment:
        return f"เกณฑ์ {criterion_id} ยังอยู่ในสถานะ {result.get('status', 'pending')} และยังไม่มีผลคะแนนจาก Phase 05."
    rationale = str(assessment.get("rationale_th") or assessment.get("reason") or "").strip()
    if rationale:
        return rationale
    score = assessment.get("score")
    return f"Phase 05 บันทึกผลการประเมินเกณฑ์ {criterion_id} ด้วยคะแนน {score}/5."


def _criterion_summary(result: dict[str, Any], narrative: str | None = None, citation_ids: list[str] | None = None) -> dict[str, Any]:
    snapshot = source_snapshot(result)
    assessment = snapshot["assessment"] or {}
    citations = citation_map(snapshot)
    # A narrative may foreground a subset, but the output always retains every
    # Phase 05 citation and its page/block provenance.
    selected_ids = list(citations)
    invalid = set(selected_ids) - set(citations)
    if invalid:
        raise ValueError(f"Summary selected citations that are not in Phase 05: {sorted(invalid)}")
    return {
        "criterion_id": snapshot["criterion"].get("criterion_id"),
        "component_id": snapshot["criterion"].get("component_id"),
        "criterion": snapshot["criterion"].get("criterion"),
        "status": snapshot["status"],
        "score": assessment.get("score"),
        "confidence": assessment.get("confidence"),
        "summary": narrative if narrative is not None else _narrative(result),
        "strengths": _items(assessment.get("strengths")),
        "areas_for_improvement": _items(assessment.get("missing_points")),
        "citations": [citations[chunk_id] for chunk_id in selected_ids],
        "phase05_snapshot": snapshot,
    }


def _template_dimension(dimension: dict[str, Any], style: dict[str, Any]) -> dict[str, Any]:
    criteria = [_criterion_summary(item) for item in dimension.get("criteria", [])]
    assessed = [item for item in criteria if item["score"] is not None]
    strengths = _dedupe([value for item in criteria for value in item["strengths"]])
    improvements = _dedupe([value for item in criteria for value in item["areas_for_improvement"]])
    dimension_id = dimension.get("dimension_id")
    if assessed:
        text = f"มิติ {dimension_id} สรุปจากผล Phase 05 จำนวน {len(assessed)} เกณฑ์ โดยเรียงประเด็นตามรูปแบบ {', '.join(style.get('summary_style', {}).get('structure', []))}."
    else:
        text = f"มิติ {dimension_id} จัดเตรียมโครงสรุปตามรูปแบบ Good Examples แล้ว แต่ยังไม่มีคะแนนจาก Phase 05 สำหรับสรุปผลเชิงประเมิน."
    return {
        "dimension_id": dimension_id,
        "dimension_name": dimension.get("criteria", [{}])[0].get("criterion", {}).get("dimension_name") or style.get("name_th"),
        "style_reference": style,
        "summary": text,
        "strengths": strengths,
        "areas_for_improvement": improvements,
        "criterion_summaries": criteria,
    }


def _template_overall(dimensions: list[dict[str, Any]], overall_style: dict[str, Any]) -> str:
    total = sum(len(item["criterion_summaries"]) for item in dimensions)
    assessed = sum(1 for item in dimensions for criterion in item["criterion_summaries"] if criterion["score"] is not None)
    if not assessed:
        return f"เอกสารนี้มี {total} เกณฑ์ใน D1–D8 โดย Phase 06 จัดโครงสรุปตาม Good Examples ({', '.join(overall_style.get('structure', []))}) และรอผลประเมินจาก Phase 05 ก่อนสรุปผลเชิงเนื้อหา."
    return f"สรุปนี้เรียบเรียงจากผลประเมิน Phase 05 จำนวน {assessed} จาก {total} เกณฑ์ ตามโครงสร้าง Good Examples: {', '.join(overall_style.get('structure', []))}."


def _llm_narratives(phase05: dict[str, Any], styles: dict[str, Any], settings: LLMSettings) -> dict[str, Any]:
    packet = {"phase05": phase05, "good_example_styles": styles,
              "task": "Write only summary prose and evidence chunk IDs. Do not score, reassess, add strengths, add missing points, add numbers, or add facts. Use only supplied Phase 05 assessment fields and evidence. Each cited chunk_id must come from that criterion's evidence."}
    return complete_json(settings, [
            {"role": "system", "content": "You are a Thai assessment-report editor. Good Examples control only structure and tone. Phase 05 is immutable. Return JSON: overall_summary and dimensions [{dimension_id, summary, criteria:[{criterion_id, summary, citation_chunk_ids}]}]."},
            {"role": "user", "content": json.dumps(packet, ensure_ascii=False)},
        ])


def build_summary(phase05: dict[str, Any], good_examples: dict[str, Any], provider: str = "none", model: str | None = None) -> dict[str, Any]:
    if not isinstance(phase05.get("dimensions"), list):
        raise ValueError("Input is not a Phase 05 assessment package: dimensions is required.")
    styles = load_good_example_styles(good_examples)
    style_dimensions = styles["dimensions"]
    settings = load_llm_settings(provider, model) if provider != "none" else None
    generated = _llm_narratives(phase05, styles, settings) if settings else None
    generated_dimensions = {item.get("dimension_id"): item for item in (generated or {}).get("dimensions", [])}
    dimensions = []
    for dimension in phase05["dimensions"]:
        dimension_id = dimension.get("dimension_id")
        style = style_dimensions.get(dimension_id, {"dimension_id": dimension_id, "summary_style": {}})
        built = _template_dimension(dimension, style)
        llm_dimension = generated_dimensions.get(dimension_id)
        if llm_dimension:
            built["summary"] = str(llm_dimension.get("summary", built["summary"]))
            llm_criteria = {item.get("criterion_id"): item for item in llm_dimension.get("criteria", [])}
            built["criterion_summaries"] = [
                _criterion_summary(
                    original,
                    narrative=str(llm_criteria.get(original.get("criterion", {}).get("criterion_id"), {}).get("summary", _narrative(original))),
                    citation_ids=llm_criteria.get(original.get("criterion", {}).get("criterion_id"), {}).get("citation_chunk_ids", list(citation_map(original))),
                )
                for original in dimension.get("criteria", [])
            ]
            built["strengths"] = _dedupe([value for item in built["criterion_summaries"] for value in item["strengths"]])
            built["areas_for_improvement"] = _dedupe([value for item in built["criterion_summaries"] for value in item["areas_for_improvement"]])
        dimensions.append(built)
    source_snapshots = {item["criterion_id"]: copy.deepcopy(item["phase05_snapshot"])
                        for dimension in dimensions for item in dimension["criterion_summaries"]}
    output = {
        "schema_version": SCHEMA_VERSION,
        "phase": "06",
        "assessment_id": assessment_id(phase05),
        "document_id": phase05.get("document_id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "summary_provider": settings.provider if settings else "none",
        "summary_model": settings.model if settings else None,
        "source_assessment": {"schema_version": phase05.get("schema_version"), "assessment_id": assessment_id(phase05), "criterion_snapshots": source_snapshots,
                              "criterion_snapshot_digests": {key: snapshot_digest(value) for key, value in source_snapshots.items()}},
        "good_examples_reference": {"knowledge_base": styles["knowledge_base"], "rules": styles["rules"], "overall_example": styles["overall_example"]},
        "overall_summary": str((generated or {}).get("overall_summary") or _template_overall(dimensions, styles["overall_example"])),
        "dimension_summaries": dimensions,
    }
    validate_summary_integrity(output)
    return output
