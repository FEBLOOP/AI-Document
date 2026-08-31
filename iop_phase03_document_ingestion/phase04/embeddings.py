from __future__ import annotations

import hashlib
import math
from typing import Protocol


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Stable lexical baseline, provided to keep the pipeline runnable offline."""
    def __init__(self, dimensions: int): self.dimensions = dimensions
    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vec = [0.0] * self.dimensions
            for token in text.lower().split():
                index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % self.dimensions
                vec[index] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vectors.append([round(x / norm, 8) for x in vec])
        return vectors


def create_embedder(provider: str, model: str, dimensions: int) -> Embedder:
    if provider == "hash": return HashEmbedder(dimensions)
    if provider == "sentence_transformers":
        from sentence_transformers import SentenceTransformer
        client = SentenceTransformer(model)
        class LocalEmbedder:
            def embed(self, texts): return client.encode(texts, normalize_embeddings=True).tolist()
        return LocalEmbedder()
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI()
        class OpenAIEmbedder:
            def embed(self, texts): return [x.embedding for x in client.embeddings.create(model=model, input=texts).data]
        return OpenAIEmbedder()
    raise ValueError(f"Unsupported embedding provider: {provider}")
