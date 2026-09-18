"""Environment-backed configuration for all IOP LLM stages."""

from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_LLM_MODEL = "Qwen/Qwen3-8B"
SUPPORTED_PROVIDERS = frozenset({"huggingface", "ollama", "openai"})


@dataclass(frozen=True)
class LLMSettings:
    """Connection settings shared by Phase 05 assessment and Phase 06 writing."""

    provider: str
    model: str
    base_url: str | None
    api_key: str | None
    max_new_tokens: int
    load_in_4bit: bool


def load_llm_settings(provider: str | None = None, model: str | None = None) -> LLMSettings:
    """Load LLM settings, giving explicit CLI values precedence over environment."""
    selected_provider = (provider or os.getenv("IOP_LLM_PROVIDER", "huggingface")).strip().lower()
    if selected_provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported IOP LLM provider: {selected_provider}. Expected one of: {', '.join(sorted(SUPPORTED_PROVIDERS))}.")

    selected_model = (model or os.getenv("IOP_LLM_MODEL", DEFAULT_LLM_MODEL)).strip()
    if not selected_model:
        raise ValueError("IOP_LLM_MODEL must not be empty.")

    base_url = os.getenv("IOP_LLM_BASE_URL")
    if not base_url and selected_provider == "ollama":
        base_url = "http://localhost:11434/v1"
    api_key = os.getenv("IOP_LLM_API_KEY")
    if not api_key and selected_provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
    try:
        max_new_tokens = int(os.getenv("IOP_LLM_MAX_NEW_TOKENS", "2048"))
    except ValueError as exc:
        raise ValueError("IOP_LLM_MAX_NEW_TOKENS must be an integer.") from exc
    if max_new_tokens < 1:
        raise ValueError("IOP_LLM_MAX_NEW_TOKENS must be greater than zero.")
    load_in_4bit = os.getenv("IOP_LLM_LOAD_IN_4BIT", "true").strip().lower()
    if load_in_4bit not in {"true", "false"}:
        raise ValueError("IOP_LLM_LOAD_IN_4BIT must be true or false.")
    return LLMSettings(selected_provider, selected_model, base_url, api_key, max_new_tokens, load_in_4bit == "true")
