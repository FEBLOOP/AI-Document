from llm.config import DEFAULT_LLM_MODEL, load_llm_settings


def test_llm_defaults_to_qwen3_8b(monkeypatch):
    monkeypatch.delenv("IOP_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("IOP_LLM_MODEL", raising=False)
    monkeypatch.delenv("IOP_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("IOP_LLM_MAX_NEW_TOKENS", raising=False)
    settings = load_llm_settings()
    assert settings.provider == "huggingface"
    assert settings.model == DEFAULT_LLM_MODEL == "Qwen/Qwen3-8B"
    assert settings.max_new_tokens == 2048
    assert settings.load_in_4bit is True


def test_llm_model_can_be_overridden_by_environment(monkeypatch):
    monkeypatch.setenv("IOP_LLM_MODEL", "custom-qwen")
    assert load_llm_settings().model == "custom-qwen"
