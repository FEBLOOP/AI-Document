from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    chunk_max_chars: int = 1400
    chunk_overlap_blocks: int = 1
    min_section_heading_chars: int = 3
    embedding_provider: str = "none"
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dimensions: int = 256

    @classmethod
    def load(cls, config_path: str | None = None) -> "Settings":
        values: dict = {}
        if config_path:
            values = json.loads(Path(config_path).read_text(encoding="utf-8"))
        prefixes = {
            "chunk_max_chars": "PHASE04_CHUNK_MAX_CHARS",
            "chunk_overlap_blocks": "PHASE04_CHUNK_OVERLAP_BLOCKS",
            "min_section_heading_chars": "PHASE04_MIN_SECTION_HEADING_CHARS",
            "embedding_provider": "PHASE04_EMBEDDING_PROVIDER",
            "embedding_model": "PHASE04_EMBEDDING_MODEL",
            "embedding_dimensions": "PHASE04_EMBEDDING_DIMENSIONS",
        }
        for name, env_name in prefixes.items():
            if env_name in os.environ:
                values[name] = os.environ[env_name]
        for name in ("chunk_max_chars", "chunk_overlap_blocks", "min_section_heading_chars", "embedding_dimensions"):
            if name in values:
                values[name] = int(values[name])
        return cls(**values)
