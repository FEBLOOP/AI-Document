from __future__ import annotations
import math
import re
from collections import Counter
from typing import Any


def _terms(value: str) -> list[str]:
    # Thai has no reliable whitespace segmentation in OCR, so mix word-like terms
    # with Thai character trigrams for a useful dependency-free retrieval baseline.
    latin = re.findall(r"[A-Za-z0-9]{2,}", value.lower())
    thai_runs = re.findall(r"[\u0e00-\u0e7f]{2,}", value)
    trigrams = [run[i:i + 3] for run in thai_runs for i in range(len(run) - 2)]
    return latin + thai_runs + trigrams


def retrieve(chunks: list[dict[str, Any]], query: str, top_k: int) -> list[dict[str, Any]]:
    query_terms = Counter(_terms(query))
    if not query_terms: return []
    scored = []
    for chunk in chunks:
        text_terms = Counter(_terms(chunk.get("normalized_text", "")))
        dot = sum(query_terms[t] * text_terms[t] for t in query_terms)
        if not dot: continue
        norm = math.sqrt(sum(x*x for x in query_terms.values()) * sum(x*x for x in text_terms.values()))
        scored.append((dot / norm, chunk))
    return [{"score": round(score, 5), "chunk": chunk} for score, chunk in sorted(scored, key=lambda x: x[0], reverse=True)[:top_k]]


def evidence_item(hit: dict[str, Any]) -> dict[str, Any]:
    chunk = hit["chunk"]
    return {"chunk_id": chunk["chunk_id"], "relevance_score": hit["score"], "text": chunk.get("normalized_text", ""),
            "section": chunk.get("section"), "page_start": chunk.get("page_start"), "page_end": chunk.get("page_end"),
            "source_blocks": chunk.get("source_blocks", [])}
