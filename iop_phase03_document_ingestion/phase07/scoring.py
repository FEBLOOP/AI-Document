from __future__ import annotations

import os
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from typing import Any


MATURITY_LEVELS = (
    (Decimal("1.7"), "Initial", "องค์กรอยู่ในระยะเริ่มต้นและแนวทางการจัดการนวัตกรรมยังไม่ชัดเจน"),
    (Decimal("2.5"), "Managed", "องค์กรเริ่มดำเนินการและมีแนวทางจัดการนวัตกรรมในบางส่วน"),
    (Decimal("3.3"), "Defined", "องค์กรแสดงความมุ่งมั่นและกำลังพัฒนาการจัดการนวัตกรรมให้ครอบคลุม"),
    (Decimal("4.1"), "Performed", "องค์กรดำเนินงานด้านนวัตกรรมอย่างต่อเนื่องและมีแนวโน้มเป็นระบบ"),
    (Decimal("5.0"), "Mature", "องค์กรดำเนินงานอย่างเป็นระบบและยั่งยืนจนสามารถเป็นต้นแบบได้"),
)

ROUNDING_MODES = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
}


def _round(value: float, decimals: int, mode: str) -> float:
    if mode not in ROUNDING_MODES:
        raise ValueError(f"Unsupported score rounding mode: {mode}.")
    quantum = Decimal("1").scaleb(-decimals)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUNDING_MODES[mode]))


def maturity_for_score(
    score: float,
    classification_decimals: int = 1,
    rounding_mode: str = "half_up",
) -> tuple[str, str, float]:
    """Classify a complete overall score after configurable display-band rounding."""
    if not 1 <= score <= 5:
        raise ValueError("Maturity score must be between 1 and 5.")
    classification_score = _round(score, classification_decimals, rounding_mode)
    decimal_score = Decimal(str(classification_score))
    for upper_bound, level, description in MATURITY_LEVELS:
        if decimal_score <= upper_bound:
            return level, description, classification_score
    raise ValueError("Maturity score is outside the supported ranges.")


def _rubric_criteria(rubric: dict[str, Any] | None) -> list[dict[str, str]] | None:
    if not rubric:
        return None
    manifest = rubric
    if isinstance(rubric.get("rubric"), dict):
        manifest = rubric["rubric"]
    if isinstance(manifest.get("criteria"), list):
        return [
            {
                "criterion_id": str(item.get("criterion_id", "")),
                "dimension_id": str(item.get("dimension_id", "")),
            }
            for item in manifest["criteria"]
        ]

    flattened = []
    for dimension in manifest.get("dimensions", []):
        dimension_id = str(dimension.get("id") or dimension.get("dimension_id") or "")
        for component in dimension.get("components", []):
            for criterion in component.get("criteria", []):
                flattened.append(
                    {
                        "criterion_id": str(criterion.get("id") or criterion.get("criterion_id") or ""),
                        "dimension_id": dimension_id,
                    }
                )
    return flattened or None


def calculate_scoring(
    phase06: dict[str, Any],
    rubric: dict[str, Any] | None = None,
    *,
    display_decimals: int | None = None,
    classification_decimals: int | None = None,
    rounding_mode: str | None = None,
) -> dict[str, Any]:
    """Validate Phase 05 scores and calculate dimension/overall IOP results."""
    display_decimals = display_decimals if display_decimals is not None else int(
        os.getenv("IOP_SCORE_DISPLAY_DECIMALS", "2")
    )
    classification_decimals = (
        classification_decimals
        if classification_decimals is not None
        else int(os.getenv("IOP_SCORE_CLASSIFICATION_DECIMALS", "1"))
    )
    rounding_mode = rounding_mode or os.getenv("IOP_SCORE_ROUNDING", "half_up").lower()
    if display_decimals < 0 or classification_decimals < 0:
        raise ValueError("Score decimal settings must be zero or greater.")

    embedded_rubric = phase06.get("source_assessment", {}).get("rubric")
    expected_items = _rubric_criteria(rubric) or _rubric_criteria(embedded_rubric)

    observed_items: list[tuple[str, str, dict[str, Any]]] = []
    for dimension in phase06.get("dimension_summaries", []):
        dimension_id = str(dimension.get("dimension_id", ""))
        for criterion in dimension.get("criterion_summaries", []):
            observed_items.append((str(criterion.get("criterion_id", "")), dimension_id, criterion))

    if expected_items is None:
        expected_items = [
            {"criterion_id": criterion_id, "dimension_id": dimension_id}
            for criterion_id, dimension_id, _ in observed_items
        ]

    expected_by_id: dict[str, str] = {}
    for item in expected_items:
        criterion_id = item["criterion_id"]
        dimension_id = item["dimension_id"]
        if not criterion_id or not dimension_id:
            raise ValueError("Rubric criteria must have criterion_id and dimension_id.")
        if criterion_id in expected_by_id:
            raise ValueError(f"Duplicate criterion_id in rubric: {criterion_id}.")
        expected_by_id[criterion_id] = dimension_id

    observed_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    duplicate_ids: list[str] = []
    invalid_score_ids: list[str] = []
    pending_ids: list[str] = []
    for criterion_id, dimension_id, criterion in observed_items:
        if criterion_id in observed_by_id:
            duplicate_ids.append(criterion_id)
            continue
        observed_by_id[criterion_id] = (dimension_id, criterion)
        score = criterion.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            invalid_score_ids.append(criterion_id)
        if not str(criterion.get("status", "")).startswith("assessed"):
            pending_ids.append(criterion_id)

    expected_ids = set(expected_by_id)
    observed_ids = set(observed_by_id)
    missing_ids = sorted(expected_ids - observed_ids)
    unexpected_ids = sorted(observed_ids - expected_ids)
    duplicate_ids = sorted(set(duplicate_ids))
    invalid_score_ids = sorted(set(invalid_score_ids))
    pending_ids = sorted(set(pending_ids))
    dimension_mismatch_ids = sorted(
        criterion_id
        for criterion_id in expected_ids & observed_ids
        if observed_by_id[criterion_id][0] != expected_by_id[criterion_id]
    )

    dimension_scores: dict[str, float | None] = {}
    expected_dimensions = list(dict.fromkeys(expected_by_id.values()))
    for dimension_id in expected_dimensions:
        dimension_ids = [
            criterion_id
            for criterion_id, expected_dimension in expected_by_id.items()
            if expected_dimension == dimension_id
        ]
        dimension_valid = all(
            criterion_id in observed_by_id
            and criterion_id not in invalid_score_ids
            and criterion_id not in pending_ids
            and observed_by_id[criterion_id][0] == dimension_id
            for criterion_id in dimension_ids
        )
        if not dimension_valid:
            dimension_scores[dimension_id] = None
            continue
        scores = [int(observed_by_id[criterion_id][1]["score"]) for criterion_id in dimension_ids]
        dimension_scores[dimension_id] = _round(
            sum(scores) / len(scores), display_decimals, rounding_mode
        )

    complete = bool(expected_ids) and not any(
        (
            missing_ids,
            unexpected_ids,
            duplicate_ids,
            invalid_score_ids,
            pending_ids,
            dimension_mismatch_ids,
        )
    )
    valid_scores = [
        int(criterion["score"])
        for criterion_id, (_, criterion) in observed_by_id.items()
        if criterion_id in expected_ids
        and criterion_id not in invalid_score_ids
        and criterion_id not in pending_ids
        and criterion_id not in dimension_mismatch_ids
    ]

    overall_raw = None
    overall_display = None
    classification_score = None
    maturity_level = None
    maturity_description = None
    if complete:
        overall_raw = sum(valid_scores) / len(expected_ids)
        overall_display = _round(overall_raw, display_decimals, rounding_mode)
        maturity_level, maturity_description, classification_score = maturity_for_score(
            overall_raw,
            classification_decimals=classification_decimals,
            rounding_mode=rounding_mode,
        )

    return {
        "status": "complete" if complete else "incomplete",
        "total_criteria": len(expected_ids),
        "scored_criteria": len(valid_scores),
        "overall_score_raw": overall_raw,
        "overall_score_display": overall_display,
        "classification_score": classification_score,
        "maturity_level": maturity_level,
        "maturity_description": maturity_description,
        "dimension_scores": dimension_scores,
        "missing_criterion_ids": missing_ids,
        "duplicate_criterion_ids": duplicate_ids,
        "invalid_score_criterion_ids": invalid_score_ids,
        "pending_criterion_ids": pending_ids,
        "unexpected_criterion_ids": unexpected_ids,
        "dimension_mismatch_criterion_ids": dimension_mismatch_ids,
        "rounding": {
            "mode": rounding_mode,
            "display_decimals": display_decimals,
            "classification_decimals": classification_decimals,
        },
    }
