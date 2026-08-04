"""Unit tests for src/config/settings.py."""

from pathlib import Path

import pytest

from src.config.errors import ConfigError
from src.config.settings import ApiKeysConfig, Settings, _resolve_env_vars

# A self-contained, valid settings.yaml fixture used by tmp_path-based tests so
# they're isolated from future edits to the real config/settings.yaml (see
# test_load_resolves_env_vars below — this used to load the real file directly,
# which meant any edit to config/settings.yaml, or any new ${VAR} added to it,
# broke this unit test until fixtures were updated).
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


def _set_all_required_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setenv("KAGGLE_USERNAME", "kaggle-test-user")
    monkeypatch.setenv("KAGGLE_KEY", "kaggle-test-key")


def _write_settings_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "settings.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_resolves_env_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uses a tmp_path fixture copy of settings.yaml, not the real config file,
    so this test is isolated from future edits to config/settings.yaml.
    """
    _set_all_required_env_vars(monkeypatch)
    path = _write_settings_yaml(tmp_path, VALID_SETTINGS_YAML)

    settings = Settings.load(path)

    assert settings.models.reasoning.provider == "deepseek"
    assert isinstance(settings.workspace.chroma_port, int)
    assert settings.execution.max_iterations == 10
    assert settings.api_keys.anthropic == "anthropic-test-key"
    assert settings.api_keys.deepseek == "deepseek-test-key"
    assert settings.api_keys.groq == "groq-test-key"
    assert settings.api_keys.kaggle_username == "kaggle-test-user"
    assert settings.api_keys.kaggle_key == "kaggle-test-key"


def test_load_real_settings_yaml_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration-style smoke test: loads the REAL config/settings.yaml (default
    path) with all env vars set, to catch drift between the dataclasses and the
    real file's actual structure. Deliberately does not assert on values likely
    to change (e.g. max_iterations) — only on the task's literal acceptance
    criterion, `models.reasoning.provider == "deepseek"`, which should stay
    pinned to the real file.
    """
    _set_all_required_env_vars(monkeypatch)

    settings = Settings.load()

    assert settings.models.reasoning.provider == "deepseek"


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


# --- Blocker 1: secrets must never appear in repr()/str() -------------------


def test_api_keys_repr_does_not_leak_secrets() -> None:
    api_keys = ApiKeysConfig(
        anthropic="sk-anthropic-secret",
        deepseek="sk-deepseek-secret",
        groq="sk-groq-secret",
        kaggle_username="my-kaggle-user",
        kaggle_key="sk-kaggle-secret",
    )

    assert "sk-anthropic-secret" not in repr(api_keys)
    assert "sk-deepseek-secret" not in repr(api_keys)
    assert "sk-groq-secret" not in repr(api_keys)
    assert "sk-kaggle-secret" not in repr(api_keys)
    assert "sk-anthropic-secret" not in str(api_keys)
    assert "sk-deepseek-secret" not in str(api_keys)
    assert "sk-groq-secret" not in str(api_keys)
    assert "sk-kaggle-secret" not in str(api_keys)
    # kaggle_username is not secret and should remain visible.
    assert "my-kaggle-user" in repr(api_keys)


def test_settings_repr_does_not_leak_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-secret")
    monkeypatch.setenv("GROQ_API_KEY", "sk-groq-secret")
    monkeypatch.setenv("KAGGLE_USERNAME", "my-kaggle-user")
    monkeypatch.setenv("KAGGLE_KEY", "sk-kaggle-secret")
    path = _write_settings_yaml(tmp_path, VALID_SETTINGS_YAML)

    settings = Settings.load(path)

    assert "sk-anthropic-secret" not in repr(settings)
    assert "sk-deepseek-secret" not in repr(settings)
    assert "sk-groq-secret" not in repr(settings)
    assert "sk-kaggle-secret" not in repr(settings)
    assert "sk-anthropic-secret" not in str(settings)
    assert "sk-deepseek-secret" not in str(settings)
    assert "sk-groq-secret" not in str(settings)
    assert "sk-kaggle-secret" not in str(settings)


# --- Blocker 2: malformed/empty ${VAR} references must never pass through --


@pytest.mark.parametrize(
    "raw_value",
    [
        "${DEEPSEEK-API-KEY}",  # hyphen in name: never matched, survives verbatim
        "$DEEPSEEK_API_KEY",  # no braces at all
        "${}",  # empty name
        "${1VAR}",  # digit-leading name (invalid identifier)
        "${UNCLOSED",  # missing closing brace
    ],
)
def test_malformed_env_var_reference_raises_config_error(raw_value: str) -> None:
    with pytest.raises(ConfigError, match="[Mm]alformed|unresolved"):
        _resolve_env_vars({"a": raw_value}, source="test-source")


def test_empty_but_set_env_var_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMPTY_TEST_VAR", "")

    with pytest.raises(ConfigError, match="EMPTY_TEST_VAR"):
        _resolve_env_vars({"a": "${EMPTY_TEST_VAR}"}, source="test-source")


def test_cp_env_example_default_first_run_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the exact failure mode described in the review: a
    fresh `cp .env.example .env` ships every key blank, so an env var IS set
    but empty. That must raise ConfigError, not silently resolve to ''.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "some-value")
    monkeypatch.setenv("GROQ_API_KEY", "some-value")
    monkeypatch.setenv("KAGGLE_USERNAME", "some-value")
    monkeypatch.setenv("KAGGLE_KEY", "some-value")
    path = _write_settings_yaml(tmp_path, VALID_SETTINGS_YAML)

    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        Settings.load(path)


def test_valid_env_var_with_non_empty_value_still_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VALID_TEST_VAR", "ok-value")

    resolved = _resolve_env_vars({"a": "${VALID_TEST_VAR}"}, source="test-source")

    assert resolved == {"a": "ok-value"}


# --- D: malformed settings.yaml error paths ---------------------------------


def _remove(text: str, snippet: str) -> str:
    assert snippet in text, f"snippet not found in fixture: {snippet!r}"
    return text.replace(snippet, "", 1)


MALFORMED_CASES: list[tuple[str, str, str]] = [
    (
        "deleted_optuna_section",
        _remove(
            VALID_SETTINGS_YAML,
            "\noptuna:\n  n_trials: 50\n  early_stopping_patience: 20\n",
        ),
        "optuna",
    ),
    (
        "deleted_models_section",
        _remove(
            VALID_SETTINGS_YAML,
            "models:\n"
            "  advisor:\n"
            "    provider: anthropic\n"
            "    model: claude-opus-5\n"
            "    temperature: 0.3\n"
            "    max_tokens: 4096\n"
            "  reasoning:\n"
            "    provider: deepseek\n"
            "    model: deepseek-v4-flash\n"
            "    temperature: 0.5\n"
            "  implementation:\n"
            "    provider: deepseek\n"
            "    model: deepseek-v4-flash\n"
            "    temperature: 0.2\n"
            "  research:\n"
            "    provider: deepseek\n"
            "    model: deepseek-v3-2\n"
            "    temperature: 0.5\n"
            "  fast:\n"
            "    provider: groq\n"
            "    model: llama-4-maverick\n"
            "    temperature: 0.1\n\n",
        ),
        "models",
    ),
    (
        "deleted_models_fast_subsection",
        _remove(
            VALID_SETTINGS_YAML,
            "  fast:\n    provider: groq\n    model: llama-4-maverick\n    temperature: 0.1\n",
        ),
        "fast",
    ),
    (
        "optuna_null",
        VALID_SETTINGS_YAML.replace(
            "optuna:\n  n_trials: 50\n  early_stopping_patience: 20\n", "optuna: null\n"
        ),
        "optuna",
    ),
    (
        "optuna_empty_mapping",
        VALID_SETTINGS_YAML.replace(
            "optuna:\n  n_trials: 50\n  early_stopping_patience: 20\n", "optuna: {}\n"
        ),
        "optuna",
    ),
    (
        "optuna_empty_list",
        VALID_SETTINGS_YAML.replace(
            "optuna:\n  n_trials: 50\n  early_stopping_patience: 20\n", "optuna: []\n"
        ),
        "optuna",
    ),
    (
        "optuna_wrong_type",
        VALID_SETTINGS_YAML.replace(
            "optuna:\n  n_trials: 50\n  early_stopping_patience: 20\n", "optuna: hello\n"
        ),
        "optuna",
    ),
    (
        "missing_models_advisor_temperature",
        VALID_SETTINGS_YAML.replace("    temperature: 0.3\n", "", 1),
        "temperature",
    ),
]


@pytest.mark.parametrize("case_id,yaml_text,expected_match", MALFORMED_CASES, ids=lambda v: v)
def test_malformed_settings_yaml_raises_clean_config_error(
    case_id: str,
    yaml_text: str,
    expected_match: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_all_required_env_vars(monkeypatch)
    path = _write_settings_yaml(tmp_path, yaml_text)

    with pytest.raises(ConfigError, match=expected_match):
        Settings.load(path)


def test_empty_settings_yaml_raises_config_error(tmp_path: Path) -> None:
    path = _write_settings_yaml(tmp_path, "")

    with pytest.raises(ConfigError, match="models"):
        Settings.load(path)


def test_top_level_yaml_list_raises_config_error(tmp_path: Path) -> None:
    path = _write_settings_yaml(tmp_path, "- a\n- b\n")

    with pytest.raises(ConfigError, match="mapping"):
        Settings.load(path)


def test_top_level_yaml_scalar_raises_config_error(tmp_path: Path) -> None:
    path = _write_settings_yaml(tmp_path, "just_a_string\n")

    with pytest.raises(ConfigError, match="mapping"):
        Settings.load(path)


def test_falsy_but_valid_value_is_not_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`max_critic_retries: 0` is falsy but valid — the required-field check must
    be `is None`, not truthiness, and must not reject it.
    """
    _set_all_required_env_vars(monkeypatch)
    yaml_text = VALID_SETTINGS_YAML.replace("max_critic_retries: 3", "max_critic_retries: 0")
    path = _write_settings_yaml(tmp_path, yaml_text)

    settings = Settings.load(path)

    assert settings.execution.max_critic_retries == 0
