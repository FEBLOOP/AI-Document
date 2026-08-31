from __future__ import annotations
from typing import Any


def load_criteria(rubric: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten IOP's D1-D8 > component > criterion hierarchy."""
    result = []
    for dimension in rubric.get("dimensions", []):
        dimension_id = dimension.get("id") or dimension.get("dimension_id")
        for component in dimension.get("components", []):
            for criterion in component.get("criteria", []):
                points = [p if isinstance(p, str) else p.get("description", "")for p in criterion.get("assessment_points", [])]
                result.append({"dimension_id": dimension_id, "dimension_name": dimension.get("name_th"),
                               "component_id": component.get("id"), "component_name": component.get("name_th"),
                               "criterion_id": criterion.get("id"), "criterion": criterion.get("description", ""),
                               "assessment_points": points, "supporting_documents": criterion.get("supporting_documents", [])})
    return result
