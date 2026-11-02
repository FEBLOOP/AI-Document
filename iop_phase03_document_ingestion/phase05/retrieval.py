from __future__ import annotations
import math
import re
from collections import Counter
from typing import Any

from phase04.embeddings import Embedder, create_embedder


RETRIEVAL_MODES = frozenset({"lexical", "embedding", "hybrid"})


def _terms(value: str) -> list[str]:
    # Thai has no reliable whitespace segmentation in OCR, so mix word-like terms
    # with Thai character trigrams for a useful dependency-free retrieval baseline.
    latin = re.findall(r"[A-Za-z0-9]{2,}", value.lower())
    thai_runs = re.findall(r"[\u0e00-\u0e7f]{2,}", value)
    trigrams = [run[i:i + 3] for run in thai_runs for i in range(len(run) - 2)]
    return latin + thai_runs + trigrams


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError(
            f"Embedding dimension mismatch: query={len(left)}, chunk={len(right)}."
        )
    dot = sum(x * y for x, y in zip(left, right))
    norm = math.sqrt(sum(x * x for x in left) * sum(y * y for y in right))
    return dot / norm if norm else 0.0


def _lexical_similarity(query_terms: Counter, text: str) -> float:
    if not query_terms:
        return 0.0
    text_terms = Counter(_terms(text))
    dot = sum(query_terms[term] * text_terms[term] for term in query_terms)
    if not dot:
        return 0.0
    norm = math.sqrt(
        sum(value * value for value in query_terms.values())
        * sum(value * value for value in text_terms.values())
    )
    return dot / norm if norm else 0.0


def query_embedder_from_chunks(chunks: list[dict[str, Any]]) -> Embedder:
    """Create the query embedder described by Phase 04 chunk metadata."""
    specifications = set()
    for chunk in chunks:
        embedding = chunk.get("embedding") or {}
        vector = embedding.get("vector")
        if not isinstance(vector, list) or not vector:
            continue
        specifications.add(
            (
                str(embedding.get("provider", "")),
                str(embedding.get("model", "")),
                len(vector),
            )
        )
    if not specifications:
        raise ValueError("Phase 04 chunks do not contain embeddings.")
    if len(specifications) != 1:
        raise ValueError("Phase 04 chunks contain inconsistent embedding metadata.")
    provider, model, dimensions = specifications.pop()
    if not provider or not model:
        raise ValueError("Phase 04 embedding metadata is incomplete.")
    return create_embedder(provider, model, dimensions)


def retrieve(
    chunks: list[dict[str, Any]],
    query: str,
    top_k: int,
    *,
    mode: str = "lexical",
    query_vector: list[float] | None = None,
    semantic_weight: float = 0.7,
) -> list[dict[str, Any]]:
    if mode not in RETRIEVAL_MODES:
        raise ValueError(f"Unsupported retrieval mode: {mode}.")
    if not 0 <= semantic_weight <= 1:
        raise ValueError("semantic_weight must be between 0 and 1.")
    if mode in {"embedding", "hybrid"} and query_vector is None:
        raise ValueError(f"{mode} retrieval requires a query embedding.")

    query_terms = Counter(_terms(query))
    scored = []
    for chunk in chunks:
        lexical_score = _lexical_similarity(
            query_terms,
            chunk.get("normalized_text", ""),
        )
        semantic_score = None
        embedding = chunk.get("embedding") or {}
        vector = embedding.get("vector")
        if query_vector is not None and isinstance(vector, list) and vector:
            semantic_score = max(
                0.0,
                _cosine_similarity(query_vector, [float(value) for value in vector]),
            )

        if mode == "lexical":
            score = lexical_score
        elif mode == "embedding":
            if semantic_score is None:
                continue
            score = semantic_score
        elif semantic_score is None:
            # A partially embedded Phase 04 package remains searchable.
            score = lexical_score
        else:
            score = (
                semantic_weight * semantic_score
                + (1 - semantic_weight) * lexical_score
            )
        if score <= 0:
            continue
        scored.append(
            {
                "score": round(score, 5),
                "lexical_score": round(lexical_score, 5),
                "semantic_score": (
                    round(semantic_score, 5)
                    if semantic_score is not None
                    else None
                ),
                "chunk": chunk,
            }
        )
    return sorted(scored, key=lambda hit: hit["score"], reverse=True)[:top_k]


def evidence_item(hit: dict[str, Any]) -> dict[str, Any]:
    chunk = hit["chunk"]
    return {"chunk_id": chunk["chunk_id"], "relevance_score": hit["score"],
            "lexical_score": hit.get("lexical_score"), "semantic_score": hit.get("semantic_score"),
            "text": chunk.get("normalized_text", ""),
            "section": chunk.get("section"), "page_start": chunk.get("page_start"), "page_end": chunk.get("page_end"),
            "source_blocks": chunk.get("source_blocks", [])}
