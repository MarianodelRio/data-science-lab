"""Unit tests for src/config/settings.py."""

import pytest

from src.config.errors import ConfigError
from src.config.settings import Settings, _resolve_env_vars


def _set_all_required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setenv("KAGGLE_USERNAME", "kaggle-test-user")
    monkeypatch.setenv("KAGGLE_KEY", "kaggle-test-key")


def test_load_resolves_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_all_required_env_vars(monkeypatch)

    settings = Settings.load()

    assert settings.models.reasoning.provider == "deepseek"
    assert isinstance(settings.workspace.chroma_port, int)
    assert settings.execution.max_iterations == 10
    assert settings.api_keys.anthropic == "anthropic-test-key"
    assert settings.api_keys.deepseek == "deepseek-test-key"
    assert settings.api_keys.groq == "groq-test-key"
    assert settings.api_keys.kaggle_username == "kaggle-test-user"
    assert settings.api_keys.kaggle_key == "kaggle-test-key"


def test_load_missing_env_var_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_all_required_env_vars(monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        Settings.load()


def test_env_var_resolution_walks_nested_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_TEST_VAR", "resolved-value")

    value = {
        "a": "${SOME_TEST_VAR}",
        "b": ["${SOME_TEST_VAR}", "literal"],
        "c": {"d": "${SOME_TEST_VAR}"},
        "e": 42,
    }

    resolved = _resolve_env_vars(value, source="test-source")

    assert resolved == {
        "a": "resolved-value",
        "b": ["resolved-value", "literal"],
        "c": {"d": "resolved-value"},
        "e": 42,
    }


def test_env_var_resolution_missing_var_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="MISSING_TEST_VAR"):
        _resolve_env_vars({"a": "${MISSING_TEST_VAR}"}, source="test-source")
