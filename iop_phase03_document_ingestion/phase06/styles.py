from __future__ import annotations

from typing import Any


def load_good_example_styles(good_examples: dict[str, Any]) -> dict[str, Any]:
    """Load the repository's Good Examples KB without treating it as a rubric."""
    kb = good_examples.get("knowledge_base", good_examples)
    rules = kb.get("rules", {})
    if rules.get("affects_score") or rules.get("affects_classification"):
        raise ValueError("Good Examples KB must be style-only; it cannot affect scoring or classification.")
    if not rules.get("used_for_summary_style", False):
        raise ValueError("Good Examples KB is not marked for summary-style use.")

    dimensions: dict[str, dict[str, Any]] = {}
    for dimension in kb.get("dimensions", []):
        dimension_id = dimension.get("dimension_id")
        if not dimension_id:
            continue
        dimensions[dimension_id] = {
            "dimension_id": dimension_id,
            "name_th": dimension.get("name_th"),
            "summary_style": dimension.get("summary_style", {}),
            "pattern": next(
                (item.get("extracted_pattern") for item in dimension.get("criteria", []) if item.get("extracted_pattern")),
                "",
            ),
            "evidence_pattern": next(
                (item.get("evidence_pattern") for item in dimension.get("criteria", []) if item.get("evidence_pattern")),
                "",
            ),
            "source_reference": {
                "document": (kb.get("source") or {}).get("document"),
                "dimension": dimension_id,
            },
        }
    return {
        "knowledge_base": {"name": kb.get("name"), "version": kb.get("version"), "source": kb.get("source", {})},
        "rules": rules,
        "overall_example": kb.get("overall_example", {}),
        "dimensions": dimensions,
    }
