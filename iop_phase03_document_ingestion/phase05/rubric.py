from __future__ import annotations
from typing import Any


DEFAULT_SCORING_GUIDE = {
    "1": "ไม่มีความชัดเจนว่าองค์กรได้ดำเนินการ หรือยังมีการเริ่มดำเนินการที่สอดคล้องตามเกณฑ์",
    "2": "องค์กรมีการดำเนินการบางส่วนที่สอดคล้องตามเกณฑ์ โดยอาจอยู่ในช่วงเริ่มต้นหรือเริ่มมีแนวทางดำเนินการ",
    "3": "องค์กรมีการดำเนินการที่สอดคล้องตามเกณฑ์ โดยอยู่ระหว่างการพัฒนาให้เกิดความครอบคลุมสมบูรณ์ทั่วทั้งองค์กรอย่างต่อเนื่อง",
    "4": "องค์กรมีการดำเนินการที่สอดคล้องตามเกณฑ์อย่างครบถ้วนสมบูรณ์ทั่วทั้งองค์กร โดยมีแนวโน้มที่จะพัฒนาอย่างเป็นระบบและต่อเนื่อง",
    "5": "องค์กรมีการดำเนินการที่สอดคล้องตามเกณฑ์อย่างครบถ้วนสมบูรณ์ทั่วทั้งองค์กร โดยมีความเป็นระบบ แสดงความยั่งยืน และสามารถเป็นต้นแบบได้",
}


def load_scoring_guide(rubric: dict[str, Any]) -> dict[str, str]:
    """Load and validate the five IOP scoring levels from the rubric."""
    guide = rubric.get("scoring_guide")
    if not isinstance(guide, dict):
        raise ValueError("IOP rubric must contain a scoring_guide object.")
    normalized = {str(key): str(value).strip() for key, value in guide.items()}
    if set(normalized) != {"1", "2", "3", "4", "5"} or not all(normalized.values()):
        raise ValueError("IOP scoring_guide must define non-empty levels 1 through 5.")
    return normalized


def rubric_manifest(rubric: dict[str, Any]) -> dict[str, Any]:
    criteria = load_criteria(rubric)
    return {
        "scoring_guide": load_scoring_guide(rubric),
        "criteria": [
            {
                "criterion_id": str(item["criterion_id"]),
                "dimension_id": str(item["dimension_id"]),
            }
            for item in criteria
        ],
    }


def load_criteria(rubric: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten IOP's D1-D8 > component > criterion hierarchy."""
    result = []
    for dimension in rubric.get("dimensions", []):
        dimension_id = dimension.get("id") or dimension.get("dimension_id")
        for component in dimension.get("components", []):
            for criterion in component.get("criteria", []):
                points = [p if isinstance(p, str) else p.get("description", "") for p in criterion.get("assessment_points", [])]
                result.append({"dimension_id": dimension_id, "dimension_name": dimension.get("name_th"),
                               "component_id": component.get("id"), "component_name": component.get("name_th"),
                               "criterion_id": criterion.get("id"), "criterion": criterion.get("description", ""),
                               "assessment_points": points, "supporting_documents": criterion.get("supporting_documents", [])})
    return result
