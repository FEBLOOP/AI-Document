import sys
from types import SimpleNamespace

import pytest

from llm.config import DEFAULT_ANTHROPIC_MODEL, DEFAULT_LLM_MODEL, load_llm_settings
from llm.config import LLMSettings
from llm.provider import (
    _anthropic_complete_json,
    _openai_compatible_complete_json,
    _parse_json_object,
)


def test_llm_defaults_to_qwen3_8b(monkeypatch):
    monkeypatch.delenv("IOP_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("IOP_LLM_MODEL", raising=False)
    monkeypatch.delenv("IOP_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("IOP_LLM_MAX_NEW_TOKENS", raising=False)
    settings = load_llm_settings()
    assert settings.provider == "huggingface"
    assert settings.model == DEFAULT_LLM_MODEL == "Qwen/Qwen3-8B"
    assert settings.max_new_tokens == 512
    assert settings.load_in_4bit is True


def test_llm_model_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("IOP_LLM_MODEL", "custom-qwen")
    assert load_llm_settings().model == "custom-qwen"


def test_anthropic_uses_provider_default_and_api_key(monkeypatch):
    monkeypatch.delenv("IOP_LLM_MODEL", raising=False)
    monkeypatch.delenv("IOP_LLM_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    settings = load_llm_settings("anthropic")

    assert settings.model == DEFAULT_ANTHROPIC_MODEL == "claude-sonnet-5"
    assert settings.api_key == "test-anthropic-key"


def test_anthropic_sends_system_separately_and_parses_text(monkeypatch):
    captured = {}

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text='{"ok": true}')]
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs
            self.messages = FakeMessages()

    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        SimpleNamespace(Anthropic=FakeAnthropic),
    )
    settings = LLMSettings(
        "anthropic",
        "claude-sonnet-5",
        None,
        "test-key",
        512,
        False,
    )

    result = _anthropic_complete_json(
        settings,
        [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Assess this."},
        ],
    )

    assert result == {"ok": True}
    assert captured["client_options"] == {"api_key": "test-key"}
    assert captured["system"] == "Return JSON."
    assert captured["messages"] == [
        {"role": "user", "content": "Assess this."}
    ]
    assert captured["max_tokens"] == 512


def test_ollama_disables_thinking_and_limits_output(monkeypatch):
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content='{"ok": true}')
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    settings = LLMSettings(
        "ollama",
        "qwen3:8b",
        "http://localhost:11434/v1",
        None,
        512,
        False,
    )

    assert _openai_compatible_complete_json(settings, []) == {"ok": True}
    assert captured["reasoning_effort"] == "none"
    assert captured["max_tokens"] == 512


def test_invalid_json_error_contains_bounded_raw_response():
    content = "not-json-" + "x" * 3000

    with pytest.raises(ValueError, match="Raw response") as error:
        _parse_json_object(content)

    assert "not-json" in str(error.value)
    assert len(str(error.value)) < 2100
