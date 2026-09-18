"""OpenAI-compatible JSON completion provider used by the IOP pipeline."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .config import LLMSettings


def complete_json(settings: LLMSettings, messages: list[dict[str, str]]) -> dict[str, Any]:
    """Request a JSON object from the configured Qwen provider."""
    if settings.provider == "huggingface":
        return _huggingface_complete_json(settings, messages)
    return _openai_compatible_complete_json(settings, messages)


def _openai_compatible_complete_json(settings: LLMSettings, messages: list[dict[str, str]]) -> dict[str, Any]:
    """Request a JSON object from Ollama or an OpenAI-compatible endpoint."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("LLM support requires `pip install openai>=1.0`.") from exc

    # Ollama exposes an OpenAI-compatible API at /v1. Its API key is ignored,
    # but the SDK requires a non-empty value.
    client = OpenAI(api_key=settings.api_key or "ollama", base_url=settings.base_url)
    response = client.chat.completions.create(
        model=settings.model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=messages,
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM returned an empty response.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM did not return valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object.")
    return parsed


@lru_cache(maxsize=1)
def _load_huggingface_model(model_id: str, load_in_4bit: bool) -> tuple[Any, Any]:
    """Load once per process so each criterion does not reload the 8B model."""
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as exc:
        raise RuntimeError(
            "Hugging Face support requires `pip install -r requirements-huggingface.txt`."
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model_options: dict[str, Any] = {"torch_dtype": "auto", "device_map": "auto"}
    # Qwen3-8B in full precision is too large for this workstation's 16 GB
    # system memory. Direct inference therefore requires a CUDA-capable build
    # when 4-bit loading is enabled.
    if load_in_4bit and not torch.cuda.is_available():
        raise RuntimeError(
            "IOP_LLM_LOAD_IN_4BIT=true requires CUDA-enabled PyTorch. "
            "Install a CUDA PyTorch wheel before running Qwen3-8B."
        )
    if load_in_4bit:
        model_options["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
        )
    model = AutoModelForCausalLM.from_pretrained(model_id, **model_options)
    return tokenizer, model


def _huggingface_complete_json(settings: LLMSettings, messages: list[dict[str, str]]) -> dict[str, Any]:
    """Run Qwen directly from Hugging Face using its native chat template."""
    tokenizer, model = _load_huggingface_model(settings.model, settings.load_in_4bit)
    inputs = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        # Assessment and summary stages must emit parseable JSON, not a
        # reasoning trace wrapped in Qwen's <think> tags.
        enable_thinking=False,
    ).to(model.device)
    generated_ids = model.generate(**inputs, max_new_tokens=settings.max_new_tokens)
    prompt_length = inputs["input_ids"].shape[-1]
    content = tokenizer.decode(generated_ids[0][prompt_length:], skip_special_tokens=True).strip()
    return _parse_json_object(content)


def _parse_json_object(content: str) -> dict[str, Any]:
    """Parse strict JSON, accepting a fenced object from an otherwise compliant model."""
    if content.startswith("```json") and content.endswith("```"):
        content = content.removeprefix("```json").removesuffix("```").strip()
    elif content.startswith("```") and content.endswith("```"):
        content = content.removeprefix("```").removesuffix("```").strip()
    if not content:
        raise ValueError("LLM returned an empty response.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("LLM did not return valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object.")
    return parsed
