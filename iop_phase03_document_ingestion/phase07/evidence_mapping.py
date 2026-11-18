from __future__ import annotations

from typing import Any


def strengths_for_evidence(
    criterion: dict[str, Any],
    chunk_id: Any,
) -> list[str]:
    """Return only explicit links to visible strengths and cited chunks.

    Historical or malformed optional mappings degrade to no links instead of
    guessing an association from order or text similarity.
    """
    normalized_chunk_id = str(chunk_id) if chunk_id is not None else ""
    strengths = [
        value
        for value in criterion.get("strengths", [])
        if isinstance(value, str) and value.strip()
    ]
    cited_ids = {
        str(citation.get("chunk_id"))
        for citation in criterion.get("citations", [])
        if isinstance(citation, dict) and citation.get("chunk_id") is not None
    }
    if normalized_chunk_id not in cited_ids:
        return []
    result: list[str] = []
    mapping = criterion.get("strength_evidence_mapping")
    if not isinstance(mapping, list):
        return result
    for entry in mapping:
        if not isinstance(entry, dict):
            continue
        strength = entry.get("strength")
        chunk_ids = entry.get("evidence_chunk_ids")
        if (
            isinstance(strength, str)
            and strength in strengths
            and isinstance(chunk_ids, list)
            and normalized_chunk_id in chunk_ids
            and strength not in result
        ):
            result.append(strength)
    return result
