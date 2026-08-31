from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def assessment_id(phase05: dict[str, Any]) -> str:
    """Stable ID for the immutable Phase 05 assessment supplied to this phase."""
    return phase05.get("assessment_id") or f"phase05_{hashlib.sha256(canonical_json(phase05).encode()).hexdigest()[:16]}"


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(snapshot).encode()).hexdigest()


def source_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    """The score, assessment fields, and evidence as received from Phase 05."""
    return copy.deepcopy({
        "criterion": result.get("criterion", {}),
        "assessment": result.get("assessment"),
        "status": result.get("status"),
        "evidence": result.get("evidence", []),
    })


def citation_map(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["chunk_id"]: copy.deepcopy(item) for item in result.get("evidence", []) if item.get("chunk_id")}


def validate_summary_integrity(summary: dict[str, Any]) -> None:
    """Ensure Phase 06 retained every immutable Phase 05 criterion snapshot."""
    source = summary.get("source_assessment", {})
    expected = source.get("criterion_snapshots", {})
    expected_digests = source.get("criterion_snapshot_digests", {})
    observed: dict[str, Any] = {}
    for dimension in summary.get("dimension_summaries", []):
        for criterion in dimension.get("criterion_summaries", []):
            criterion_id = criterion.get("criterion_id")
            if not criterion_id:
                raise ValueError("A criterion summary has no criterion_id.")
            observed[criterion_id] = criterion.get("phase05_snapshot")
            evidence_ids = set(citation_map(criterion.get("phase05_snapshot", {})))
            for citation in criterion.get("citations", []):
                if citation.get("chunk_id") not in evidence_ids:
                    raise ValueError(f"Citation {citation.get('chunk_id')} is not Phase 05 evidence for {criterion_id}.")
    if set(expected) != set(observed):
        raise ValueError("Phase 06 output does not contain exactly the Phase 05 criteria.")
    for criterion_id, snapshot in expected.items():
        if expected_digests.get(criterion_id) != snapshot_digest(snapshot):
            raise ValueError(f"Stored Phase 05 snapshot digest is invalid for {criterion_id}.")
        if canonical_json(snapshot) != canonical_json(observed[criterion_id]):
            raise ValueError(f"Phase 06 attempted to change Phase 05 score, assessment, or evidence for {criterion_id}.")
