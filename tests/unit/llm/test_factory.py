"""Unit tests for src/llm/factory.py.

All provider chat-model classes are mocked at their import location inside
`src.llm.factory` — no real network calls, no real API keys required.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from src.config.settings import Settings
from src.llm import factory as factory_module
from src.llm.factory import LLMFactory

# Mirrors tests/unit/config/test_settings.py's VALID_SETTINGS_YAML fixture pattern:
# a self-contained settings.yaml so this test file is isolated from future edits
# to the real config/settings.yaml. Only `advisor` sets `max_tokens` here, matching
# the real file.
VALID_SETTINGS_YAML = """\
models:
  advisor:
    provider: anthropic
    model: claude-opus-5
    temperature: 0.3
    max_tokens: 4096
  reasoning:
    provider: deepseek
    model: deepseek-v4-flash
    temperature: 0.5
  implementation:
    provider: deepseek
    model: deepseek-v4-flash
    temperature: 0.2
  research:
    provider: deepseek
    model: deepseek-v3-2
    temperature: 0.5
  fast:
    provider: groq
    model: llama-4-maverick
    temperature: 0.1

api_keys:
  anthropic: ${ANTHROPIC_API_KEY}
  deepseek: ${DEEPSEEK_API_KEY}
  groq: ${GROQ_API_KEY}
  kaggle_username: ${KAGGLE_USERNAME}
  kaggle_key: ${KAGGLE_KEY}

context:
  trim_strategy: last_n_messages
  max_messages_per_node: 10

workspace:
  root: /competitions
  chroma_host: chroma
  chroma_port: 8000
  mlflow_tracking_uri: http://mlflow:5000

optuna:
  n_trials: 50
  early_stopping_patience: 20

execution:
  max_parallel_agents: 2
  code_executor_timeout_seconds: 3600
  max_critic_retries: 3
  max_iterations: 10
"""


@pytest.fixture(autouse=True)
def reset_factory_cache() -> Iterator[None]:
    """Ensure LLMFactory's class-level Settings cache never leaks across tests."""
    LLMFactory._settings = None
    yield
    LLMFactory._settings = None


@pytest.fixture
def settings_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setenv("KAGGLE_USERNAME", "kaggle-test-user")
    monkeypatch.setenv("KAGGLE_KEY", "kaggle-test-key")
    path = tmp_path / "settings.yaml"
    path.write_text(VALID_SETTINGS_YAML, encoding="utf-8")
    return path


@pytest.fixture
def patched_settings_load(settings_path: Path, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch `Settings.load` (as seen from src.llm.factory) so LLMFactory reads the
    fixture file regardless of the default path argument, and expose the mock for
    call-count assertions.
    """
    real_settings = Settings.load(settings_path)
    mock_load = MagicMock(return_value=real_settings)
    monkeypatch.setattr(factory_module.Settings, "load", mock_load)
    return mock_load


def test_reasoning_role_dispatches_deepseek_via_chatopenai(
    patched_settings_load: MagicMock,
) -> None:
    with patch("src.llm.factory.ChatOpenAI") as mock_chat_openai:
        LLMFactory.get("reasoning")

    mock_chat_openai.assert_called_once()
    _, kwargs = mock_chat_openai.call_args
    assert kwargs["base_url"] == "https://api.deepseek.com"
    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["api_key"] == SecretStr("deepseek-test-key")


def test_advisor_role_dispatches_anthropic(patched_settings_load: MagicMock) -> None:
    with patch("src.llm.factory.ChatAnthropic") as mock_chat_anthropic:
        LLMFactory.get("advisor")

    mock_chat_anthropic.assert_called_once()
    _, kwargs = mock_chat_anthropic.call_args
    assert kwargs["model"] == "claude-opus-5"
    assert kwargs["api_key"] == SecretStr("anthropic-test-key")


def test_unknown_role_raises_keyerror(patched_settings_load: MagicMock) -> None:
    with pytest.raises(KeyError):
        LLMFactory.get("not_a_real_role")


def test_unknown_provider_raises_valueerror(patched_settings_load: MagicMock) -> None:
    settings = patched_settings_load.return_value
    bogus_role_config = replace(settings.models.reasoning, provider="bogus")
    bogus_models = replace(settings.models, reasoning=bogus_role_config)
    bogus_settings = replace(settings, models=bogus_models)
    patched_settings_load.return_value = bogus_settings

    with pytest.raises(ValueError, match="bogus"):
        LLMFactory.get("reasoning")


def test_implementation_role_dispatches_deepseek(patched_settings_load: MagicMock) -> None:
    with patch("src.llm.factory.ChatOpenAI") as mock_chat_openai:
        LLMFactory.get("implementation")

    mock_chat_openai.assert_called_once()
    _, kwargs = mock_chat_openai.call_args
    assert kwargs["base_url"] == "https://api.deepseek.com"
    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["temperature"] == 0.2


def test_research_role_dispatches_deepseek(patched_settings_load: MagicMock) -> None:
    with patch("src.llm.factory.ChatOpenAI") as mock_chat_openai:
        LLMFactory.get("research")

    mock_chat_openai.assert_called_once()
    _, kwargs = mock_chat_openai.call_args
    assert kwargs["base_url"] == "https://api.deepseek.com"
    assert kwargs["model"] == "deepseek-v3-2"


def test_fast_role_dispatches_groq(patched_settings_load: MagicMock) -> None:
    with patch("src.llm.factory.ChatGroq") as mock_chat_groq:
        LLMFactory.get("fast")

    mock_chat_groq.assert_called_once()
    _, kwargs = mock_chat_groq.call_args
    assert kwargs["model"] == "llama-4-maverick"
    assert kwargs["api_key"] == SecretStr("groq-test-key")


def test_openai_provider_dispatch(patched_settings_load: MagicMock) -> None:
    settings = patched_settings_load.return_value
    openai_role_config = replace(settings.models.reasoning, provider="openai", model="gpt-5")
    openai_models = replace(settings.models, reasoning=openai_role_config)
    patched_settings_load.return_value = replace(settings, models=openai_models)

    with patch("src.llm.factory.ChatOpenAI") as mock_chat_openai:
        LLMFactory.get("reasoning")

    mock_chat_openai.assert_called_once()
    _, kwargs = mock_chat_openai.call_args
    assert kwargs["model"] == "gpt-5"
    # No settings.api_keys.openai field exists — no api_key kwarg is passed,
    # relying on ChatOpenAI's own OPENAI_API_KEY env fallback.
    assert "api_key" not in kwargs
    assert "base_url" not in kwargs


def test_gemini_provider_dispatch(patched_settings_load: MagicMock) -> None:
    settings = patched_settings_load.return_value
    gemini_role_config = replace(
        settings.models.reasoning, provider="gemini", model="gemini-2.5-pro"
    )
    gemini_models = replace(settings.models, reasoning=gemini_role_config)
    patched_settings_load.return_value = replace(settings, models=gemini_models)

    with patch("src.llm.factory.ChatGoogleGenerativeAI") as mock_chat_gemini:
        LLMFactory.get("reasoning")

    mock_chat_gemini.assert_called_once()
    _, kwargs = mock_chat_gemini.call_args
    assert kwargs["model"] == "gemini-2.5-pro"
    # No settings.api_keys.gemini field exists — relies on the SDK's own
    # GOOGLE_API_KEY env fallback, same gap as the openai provider.
    assert "api_key" not in kwargs
    assert "google_api_key" not in kwargs


def test_max_tokens_omitted_when_none(patched_settings_load: MagicMock) -> None:
    # `reasoning` sets no max_tokens in the fixture yaml.
    with patch("src.llm.factory.ChatOpenAI") as mock_chat_openai:
        LLMFactory.get("reasoning")

    _, kwargs = mock_chat_openai.call_args
    assert "max_tokens" not in kwargs


def test_max_tokens_present_when_set(patched_settings_load: MagicMock) -> None:
    # `advisor` sets max_tokens: 4096 in the fixture yaml.
    with patch("src.llm.factory.ChatAnthropic") as mock_chat_anthropic:
        LLMFactory.get("advisor")

    _, kwargs = mock_chat_anthropic.call_args
    assert kwargs["max_tokens"] == 4096


def test_settings_load_called_once_across_multiple_get_calls(
    patched_settings_load: MagicMock,
) -> None:
    with (
        patch("src.llm.factory.ChatOpenAI"),
        patch("src.llm.factory.ChatAnthropic"),
        patch("src.llm.factory.ChatGroq"),
    ):
        LLMFactory.get("reasoning")
        LLMFactory.get("advisor")
        LLMFactory.get("fast")

    assert patched_settings_load.call_count == 1


def test_import_alone_constructs_no_client() -> None:
    """A fresh import of src.llm.factory (with every provider class mocked at their
    origin) must not construct any client — module-level code only defines the
    class/dispatch dicts, it never instantiates a provider.

    Imports under a popped `sys.modules` entry rather than `importlib.reload` so the
    `factory_module`/`LLMFactory` objects other tests in this file already bound at
    collection time are left completely untouched.
    """
    module_name = "src.llm.factory"
    original_module = sys.modules.pop(module_name, None)
    try:
        with (
            patch("langchain_openai.ChatOpenAI") as mock_openai,
            patch("langchain_anthropic.ChatAnthropic") as mock_anthropic,
            patch("langchain_groq.ChatGroq") as mock_groq,
            patch("langchain_google_genai.ChatGoogleGenerativeAI") as mock_gemini,
        ):
            importlib.import_module(module_name)
    finally:
        sys.modules.pop(module_name, None)
        if original_module is not None:
            sys.modules[module_name] = original_module

    mock_openai.assert_not_called()
    mock_anthropic.assert_not_called()
    mock_groq.assert_not_called()
    mock_gemini.assert_not_called()
