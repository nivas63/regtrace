from __future__ import annotations

import pytest

from app.config import LLMProviderName, Settings


def test_defaults_prefer_free_providers() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.llm_reasoning_chain[0] is LLMProviderName.GEMINI
    assert "gemini:gemini-2.5-flash" in settings.llm_rate_limits


def test_env_overrides_chain_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_REASONING_CHAIN", '["groq","gemini"]')
    monkeypatch.setenv("LLM_RATE_LIMITS", '{"groq:x": {"rpm": 5}}')
    monkeypatch.setenv("GEMINI_API_KEY", "secret-value")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.llm_reasoning_chain == [LLMProviderName.GROQ, LLMProviderName.GEMINI]
    assert settings.llm_rate_limits["groq:x"].rpm == 5
    # Secrets never appear in repr/logs.
    assert "secret-value" not in repr(settings)
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "secret-value"


def test_empty_env_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_THINKING_BUDGET", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.gemini_api_key is None
    assert settings.gemini_thinking_budget is None


def test_invalid_chain_value_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_FAST_CHAIN", '["openai-paid"]')
    with pytest.raises(ValueError, match="llm_fast_chain"):
        Settings(_env_file=None)  # type: ignore[call-arg]
