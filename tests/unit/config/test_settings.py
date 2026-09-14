"""Unit tests for src/config/settings.py."""

from pathlib import Path

import pytest

from src.config.errors import ConfigError
from src.config.settings import (
    ApiKeysConfig,
    Settings,
    _missing_api_key_descriptors,
    _resolve_env_vars,
)

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


# --- T-048: Settings.validate_required_keys() -------------------------------


def test_validate_required_keys_passes_when_all_keys_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_all_required_env_vars(monkeypatch)
    path = _write_settings_yaml(tmp_path, VALID_SETTINGS_YAML)

    assert Settings.validate_required_keys(path) is None


def test_validate_required_keys_raises_when_one_key_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_all_required_env_vars(monkeypatch)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    path = _write_settings_yaml(tmp_path, VALID_SETTINGS_YAML)

    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        Settings.validate_required_keys(path)


def test_validate_required_keys_raises_when_one_key_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_all_required_env_vars(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "")
    path = _write_settings_yaml(tmp_path, VALID_SETTINGS_YAML)

    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        Settings.validate_required_keys(path)


def test_validate_required_keys_reports_all_missing_keys_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The test that actually proves the core requirement: both failures must
    appear in the same ConfigError. A test that only checked one substring
    would still pass if the implementation regressed to raise-on-first.
    """
    _set_all_required_env_vars(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("KAGGLE_KEY", "")
    path = _write_settings_yaml(tmp_path, VALID_SETTINGS_YAML)

    with pytest.raises(ConfigError) as exc_info:
        Settings.validate_required_keys(path)

    assert "ANTHROPIC_API_KEY" in str(exc_info.value)
    assert "KAGGLE_KEY" in str(exc_info.value)


def test_validate_required_keys_raises_when_api_keys_section_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_all_required_env_vars(monkeypatch)
    yaml_text = _remove(
        VALID_SETTINGS_YAML,
        "api_keys:\n"
        "  anthropic: ${ANTHROPIC_API_KEY}\n"
        "  deepseek: ${DEEPSEEK_API_KEY}\n"
        "  groq: ${GROQ_API_KEY}\n"
        "  kaggle_username: ${KAGGLE_USERNAME}\n"
        "  kaggle_key: ${KAGGLE_KEY}\n\n",
    )
    path = _write_settings_yaml(tmp_path, yaml_text)

    with pytest.raises(ConfigError, match="api_keys"):
        Settings.validate_required_keys(path)


def test_validate_required_keys_ignores_errors_in_other_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Locks in the keys-only preflight design decision: a malformed section
    outside `api_keys` must not fail validate_required_keys(), even though the
    same fixture fails Settings.load().
    """
    _set_all_required_env_vars(monkeypatch)
    yaml_text = _remove(
        VALID_SETTINGS_YAML,
        "\noptuna:\n  n_trials: 50\n  early_stopping_patience: 20\n",
    )
    path = _write_settings_yaml(tmp_path, yaml_text)

    assert Settings.validate_required_keys(path) is None
    with pytest.raises(ConfigError, match="optuna"):
        Settings.load(path)


def test_validate_required_keys_default_path_uses_real_settings_yaml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors test_load_real_settings_yaml_smoke: confirms the classmethod
    works against the real config/settings.yaml, not just fixtures.
    """
    _set_all_required_env_vars(monkeypatch)

    assert Settings.validate_required_keys() is None


def test_validate_required_keys_does_not_require_prior_settings_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The point of this test: validate_required_keys() is called as the very
    # first line of the test body, with no preceding Settings.load() anywhere.
    _set_all_required_env_vars(monkeypatch)
    path = _write_settings_yaml(tmp_path, VALID_SETTINGS_YAML)

    assert Settings.validate_required_keys(path) is None


def test_validate_required_keys_accepts_nonstring_value_that_load_also_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for ADV-61c41fff: validate_required_keys() and load()
    must agree on what's a valid api_keys value. A bare unquoted numeric
    literal (e.g. `kaggle_username: 123456`) is parsed by yaml.safe_load as a
    Python int -- Settings.load() accepts it (dataclasses don't enforce field
    types at runtime), so the preflight check must accept it too, not flag it
    as a false-positive "missing" key.
    """
    _set_all_required_env_vars(monkeypatch)
    yaml_text = VALID_SETTINGS_YAML.replace(
        "kaggle_username: ${KAGGLE_USERNAME}", "kaggle_username: 123456"
    )
    path = _write_settings_yaml(tmp_path, yaml_text)

    settings = Settings.load(path)

    assert settings.api_keys.kaggle_username == 123456  # type: ignore[comparison-overlap]
    assert Settings.validate_required_keys(path) is None


# --- T-048: _missing_api_key_descriptors() -----------------------------------


def test_missing_api_key_descriptors_returns_empty_when_all_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "b")
    monkeypatch.setenv("GROQ_API_KEY", "c")
    monkeypatch.setenv("KAGGLE_USERNAME", "d")
    monkeypatch.setenv("KAGGLE_KEY", "e")
    raw_api_keys = {
        "anthropic": "${ANTHROPIC_API_KEY}",
        "deepseek": "${DEEPSEEK_API_KEY}",
        "groq": "${GROQ_API_KEY}",
        "kaggle_username": "${KAGGLE_USERNAME}",
        "kaggle_key": "${KAGGLE_KEY}",
    }

    assert _missing_api_key_descriptors(raw_api_keys) == []


def test_missing_api_key_descriptors_flags_field_absent_from_dict() -> None:
    raw_api_keys = {
        "anthropic": "literal-anthropic",
        "deepseek": "literal-deepseek",
        "kaggle_username": "literal-kaggle-user",
        "kaggle_key": "literal-kaggle-key",
        # "groq" intentionally omitted entirely
    }

    assert _missing_api_key_descriptors(raw_api_keys) == ["api_keys.groq"]


def test_missing_api_key_descriptors_flags_unset_env_var() -> None:
    raw_api_keys = {
        "anthropic": "${SOME_UNSET_VAR}",
        "deepseek": "literal-deepseek",
        "groq": "literal-groq",
        "kaggle_username": "literal-kaggle-user",
        "kaggle_key": "literal-kaggle-key",
    }

    assert _missing_api_key_descriptors(raw_api_keys) == ["api_keys.anthropic (${SOME_UNSET_VAR})"]


def test_missing_api_key_descriptors_flags_empty_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMPTY_KEY_VAR", "")
    raw_api_keys = {
        "anthropic": "${EMPTY_KEY_VAR}",
        "deepseek": "literal-deepseek",
        "groq": "literal-groq",
        "kaggle_username": "literal-kaggle-user",
        "kaggle_key": "literal-kaggle-key",
    }

    assert _missing_api_key_descriptors(raw_api_keys) == ["api_keys.anthropic (${EMPTY_KEY_VAR})"]


def test_missing_api_key_descriptors_accepts_nonempty_literal_value() -> None:
    raw_api_keys = {
        "anthropic": "hardcoded-literal-value",
        "deepseek": "literal-deepseek",
        "groq": "literal-groq",
        "kaggle_username": "literal-kaggle-user",
        "kaggle_key": "literal-kaggle-key",
    }

    assert _missing_api_key_descriptors(raw_api_keys) == []


def test_missing_api_key_descriptors_flags_empty_literal_value() -> None:
    raw_api_keys = {
        "anthropic": "",
        "deepseek": "literal-deepseek",
        "groq": "literal-groq",
        "kaggle_username": "literal-kaggle-user",
        "kaggle_key": "literal-kaggle-key",
    }

    assert _missing_api_key_descriptors(raw_api_keys) == ["api_keys.anthropic"]


def test_missing_api_key_descriptors_flags_none_value() -> None:
    """A field explicitly set to null in YAML must be flagged, distinct from a
    field absent from the dict entirely (see
    test_missing_api_key_descriptors_flags_field_absent_from_dict) -- both
    collapse to the same "present and not None" check but are worth locking
    in as separate cases.
    """
    raw_api_keys = {
        "anthropic": None,
        "deepseek": "literal-deepseek",
        "groq": "literal-groq",
        "kaggle_username": "literal-kaggle-user",
        "kaggle_key": "literal-kaggle-key",
    }

    assert _missing_api_key_descriptors(raw_api_keys) == ["api_keys.anthropic"]


@pytest.mark.parametrize("raw_value", [123456, True])
def test_missing_api_key_descriptors_accepts_nonstring_present_value(raw_value: object) -> None:
    """Regression test for ADV-61c41fff: a present, non-None, non-string value
    (e.g. an int like a numeric Kaggle username, or a bool) must NOT be
    flagged as missing. Settings.load()'s _require_field has no type check
    and accepts any present, non-None value, and _resolve_env_vars leaves
    non-str/dict/list values unchanged -- this preflight check must not be
    stricter than what load() itself actually accepts.
    """
    raw_api_keys = {
        "anthropic": raw_value,
        "deepseek": "literal-deepseek",
        "groq": "literal-groq",
        "kaggle_username": "literal-kaggle-user",
        "kaggle_key": "literal-kaggle-key",
    }

    assert _missing_api_key_descriptors(raw_api_keys) == []


def test_missing_api_key_descriptors_flags_only_the_unset_var_in_a_multi_var_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Names deliberately share no substring with each other (unlike "SET_VAR"/
    # "UNSET_VAR") so the assertions below can't pass on a substring accident.
    monkeypatch.setenv("PRESENT_VAR", "value")
    monkeypatch.delenv("MISSING_VAR", raising=False)
    raw_api_keys = {
        "anthropic": "${PRESENT_VAR}${MISSING_VAR}",
        "deepseek": "literal-deepseek",
        "groq": "literal-groq",
        "kaggle_username": "literal-kaggle-user",
        "kaggle_key": "literal-kaggle-key",
    }

    result = _missing_api_key_descriptors(raw_api_keys)

    assert result == ["api_keys.anthropic (${MISSING_VAR})"]
    assert "PRESENT_VAR" not in result[0]
