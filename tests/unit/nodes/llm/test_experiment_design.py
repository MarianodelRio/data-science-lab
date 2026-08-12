"""Unit tests for src/nodes/llm/_experiment_design.py — the `design.json`
contract shared by every Pipeline Phase 5 specialist.

No LLM, no network, no mocks: every function here is either pure data
transformation/validation or does real filesystem I/O against a
`tmp_path`-backed `WorkspaceManager` (the `test_research_common.py`
precedent).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from src.nodes.llm._experiment_design import (
    CV_STRATEGY_REF,
    DESIGN_KEYS,
    FORBIDDEN_CV_KEYS,
    extract_json_object,
    normalize_model_family,
    read_fold_summary,
    read_solution_plan,
    resolve_feature_spec_ref,
    validate_experiment_design,
)
from src.nodes.llm.classical_ml_specialist import _MODEL_FAMILIES as FAMILIES
from src.state import LabState, new_state
from src.workspace.workspace_manager import WorkspaceManager

SPECIALIST = "classical_ml_specialist"
# `FAMILIES` is the *production* table imported from the node, never a copy:
# a hand-copied table let a mutation of the real one (gutted families, typo'd
# aliases, substring instead of word-boundary matching) leave the whole suite
# green.
_ALIAS_CASES = [(alias, family) for family, aliases in FAMILIES.items() for alias in aliases]
FEATURE_SPEC_REF = "design/iteration_0/feature_spec.json"
_NOT_AVAILABLE = "(frozen fold config not yet available)"

VALID_DESIGN: dict[str, Any] = {
    "model_family": "lightgbm",
    "search_space": {
        "n_estimators": {"type": "int", "low": 100, "high": 1000, "step": 50},
        "learning_rate": {"type": "float", "low": 0.001, "high": 0.3, "log": True},
        "boosting_type": {"type": "categorical", "choices": ["gbdt", "dart"]},
    },
    "fixed_params": {"objective": "binary"},
    "preprocessing": ["native_categorical_handling"],
    "rationale": "Mixed-type tabular data, handled natively by LightGBM.",
}


def _design(**overrides: Any) -> dict[str, Any]:
    data = copy.deepcopy(VALID_DESIGN)
    data.update(overrides)
    return data


def _validate(data: dict[str, Any], feature_spec_ref: str = FEATURE_SPEC_REF) -> dict[str, Any]:
    return validate_experiment_design(
        data,
        specialist=SPECIALIST,
        allowed_families=FAMILIES,
        feature_spec_ref=feature_spec_ref,
    )


def _validate_param(spec: Any) -> dict[str, Any]:
    """Validate a design whose entire search space is the single param `p`."""
    return _validate(_design(search_space={"p": spec}))


# -- extract_json_object (fence handling is exercised through it, not directly) --


def test_extract_json_object_parses_unfenced() -> None:
    assert extract_json_object('{"a": 1}', SPECIALIST) == {"a": 1}


def test_extract_json_object_strips_json_fence() -> None:
    assert extract_json_object('```json\n{"a": 1}\n```', SPECIALIST) == {"a": 1}


def test_extract_json_object_salvages_truncated_fence() -> None:
    """A fence the response never closed is one of the two most common postamble
    shapes — the salvage must be reachable even though `_strip_outer_fence`
    raises first."""
    assert extract_json_object('```json\n{"a": 1}', SPECIALIST) == {"a": 1}


def test_extract_json_object_salvages_sentence_after_closed_fence() -> None:
    content = '```json\n{"a": 1}\n```\nHope that helps!'

    assert extract_json_object(content, SPECIALIST) == {"a": 1}


def test_extract_json_object_unclosed_fence_without_json_raises() -> None:
    with pytest.raises(ValueError, match="never closes it"):
        extract_json_object("```json\nstill thinking about it", SPECIALIST)


def test_extract_json_object_single_line_fence_raises() -> None:
    with pytest.raises(ValueError, match="fence has no content"):
        extract_json_object("``````", SPECIALIST)


def test_extract_json_object_rejects_array() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        extract_json_object('[{"a": 1}]', SPECIALIST)


def test_extract_json_object_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match=SPECIALIST):
        extract_json_object("not json at all {", SPECIALIST)


def test_extract_json_object_salvages_preamble_before_fence() -> None:
    """One sentence of prose before the JSON must not abort a node that has no
    retry wrapper."""
    content = 'Here is the design you asked for:\n\n```json\n{"a": 1}\n```'

    assert extract_json_object(content, SPECIALIST) == {"a": 1}


def test_extract_json_object_salvages_trailing_sentence() -> None:
    content = '{"a": 1}\n\nLet me know if you want a wider search space.'

    assert extract_json_object(content, SPECIALIST) == {"a": 1}


def test_extract_json_object_prose_only_refusal_still_raises() -> None:
    with pytest.raises(ValueError, match="is not valid JSON"):
        extract_json_object("I cannot design this experiment without a dataset.", SPECIALIST)


def test_extract_json_object_reports_the_original_parse_error() -> None:
    """When the salvage attempt also fails, the message describes the response as
    a whole, not the salvaged slice."""
    with pytest.raises(ValueError, match="is not valid JSON"):
        extract_json_object('prose { "a": 1, } more prose', SPECIALIST)


def test_extract_json_object_rejects_oversized_integer_literal() -> None:
    """CPython's 4300-digit int conversion limit makes `json.loads` raise a bare
    `ValueError`, not a `JSONDecodeError` — it must still be wrapped and
    attributed to the specialist."""
    content = '{"a": ' + "9" * 5000 + "}"

    with pytest.raises(ValueError, match=SPECIALIST):
        extract_json_object(content, SPECIALIST)


# -- whitelist rebuild --


def test_valid_payload_returns_exactly_contract_keys() -> None:
    result = _validate(_design())

    assert tuple(result) == DESIGN_KEYS


def test_injected_fields_override_llm_values() -> None:
    result = _validate(
        _design(
            specialist="nlp_specialist",
            feature_spec_ref="/somewhere/else.json",
            cv_strategy_ref="my_own_folds.json",
        )
    )

    assert result["specialist"] == SPECIALIST
    assert result["feature_spec_ref"] == FEATURE_SPEC_REF
    assert result["cv_strategy_ref"] == CV_STRATEGY_REF


def test_cv_strategy_ref_key_is_not_treated_as_forbidden() -> None:
    """Regression guard for the exact-key-name rule: `cv_strategy` is forbidden,
    but `cv_strategy_ref` is the pipeline's own injected pointer and must be
    silently ignored, not rejected as a CV redefinition."""
    result = _validate(_design(cv_strategy_ref="whatever the LLM felt like"))

    assert result["cv_strategy_ref"] == CV_STRATEGY_REF


def test_unknown_top_level_keys_dropped() -> None:
    result = _validate(
        _design(n_trials=500, early_stopping_patience=99, notes="chatty extra field")
    )

    assert "n_trials" not in result
    assert "early_stopping_patience" not in result
    assert "notes" not in result


# -- model_family normalization --


@pytest.mark.parametrize(("alias", "expected"), _ALIAS_CASES)
def test_every_production_alias_resolves_to_its_family(alias: str, expected: str) -> None:
    """Parametrized over the real `_MODEL_FAMILIES` table, so shrinking, typo'ing
    or deleting an entry fails a test instead of passing silently."""
    assert normalize_model_family(alias, FAMILIES, SPECIALIST) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("XGBoost", "xgboost"),
        ("LGBM", "lightgbm"),
        ("light-gbm", "lightgbm"),
        ("CatBoost", "catboost"),
        ("extra-trees", "extra_trees"),
        ("ExtraTrees", "extra_trees"),
    ],
)
def test_model_family_casing_and_separator_variants_normalize(raw: str, expected: str) -> None:
    assert normalize_model_family(raw, FAMILIES, SPECIALIST) == expected


@pytest.mark.parametrize("raw", ["LGBMClassifier", "xgboostiness", "precatboost"])
def test_model_family_substring_without_word_boundary_is_rejected(raw: str) -> None:
    """Matching is whole-phrase: a class name that merely *contains* an alias is
    not a family declaration. Swapping the word-boundary regex for a substring
    check would silently accept all of these."""
    with pytest.raises(ValueError, match="not a supported model family"):
        normalize_model_family(raw, FAMILIES, SPECIALIST)


def test_all_four_supported_families_are_present() -> None:
    assert set(FAMILIES) == {"xgboost", "lightgbm", "catboost", "extra_trees"}


@pytest.mark.parametrize("raw", ["random_forest", "neural_network"])
def test_model_family_unsupported_raises(raw: str) -> None:
    with pytest.raises(ValueError, match="not a supported model family"):
        normalize_model_family(raw, FAMILIES, SPECIALIST)


def test_model_family_ambiguous_raises() -> None:
    """Two families named at once is rejected, never resolved by precedence —
    `coder` dispatches on this value, so a wrong-but-plausible pick is worse
    than a loud failure."""
    with pytest.raises(ValueError, match="ambiguous"):
        normalize_model_family("xgboost or lightgbm", FAMILIES, SPECIALIST)


@pytest.mark.parametrize("raw", [None, 42, "", "   ", ["xgboost"]])
def test_model_family_non_string_raises(raw: Any) -> None:
    with pytest.raises(ValueError, match="model_family"):
        normalize_model_family(raw, FAMILIES, SPECIALIST)


def test_written_model_family_is_canonical_not_raw() -> None:
    result = _validate(_design(model_family="XGB"))

    assert result["model_family"] == "xgboost"


# -- search_space shape --


@pytest.mark.parametrize("value", [None, {}])
def test_search_space_missing_or_empty_raises(value: Any) -> None:
    data = _design()
    if value is None:
        del data["search_space"]
    else:
        data["search_space"] = value

    with pytest.raises(ValueError, match="search_space"):
        _validate(data)


def test_search_space_not_a_dict_raises() -> None:
    with pytest.raises(ValueError, match="search_space"):
        _validate(_design(search_space=[{"type": "int", "low": 1, "high": 2}]))


def test_param_spec_bare_tuple_raises() -> None:
    with pytest.raises(ValueError, match="must be an object with a 'type' field"):
        _validate_param([0.001, 0.1])


def test_param_spec_expression_string_raises() -> None:
    with pytest.raises(ValueError, match="must be an object with a 'type' field"):
        _validate_param("trial.suggest_float('p', 1e-3, 1e-1, log=True)")


def test_param_spec_distribution_string_raises() -> None:
    with pytest.raises(ValueError, match="must be an object with a 'type' field"):
        _validate_param("loguniform(1e-3,1e-1)")


@pytest.mark.parametrize("param_type", ["loguniform", "uniform", "int_uniform", 7, None])
def test_param_type_unknown_raises(param_type: Any) -> None:
    with pytest.raises(ValueError, match="unsupported 'type'"):
        _validate_param({"type": param_type, "low": 1, "high": 10})


# -- numeric param bounds --


@pytest.mark.parametrize("bound", ["low", "high"])
def test_int_bounds_reject_bool(bound: str) -> None:
    """`isinstance(True, int)` is True in Python — a JSON boolean must be
    rejected explicitly before the numeric check, never silently treated as
    0/1."""
    spec = {"type": "int", "low": 1, "high": 10}
    spec[bound] = True  # type: ignore[assignment]

    with pytest.raises(ValueError, match="boolean"):
        _validate_param(spec)


@pytest.mark.parametrize("bound", ["low", "high"])
def test_numeric_bound_required(bound: str) -> None:
    spec = {"type": "float", "low": 0.1, "high": 0.9}
    del spec[bound]

    with pytest.raises(ValueError, match=bound):
        _validate_param(spec)


@pytest.mark.parametrize("bound", ["low", "high"])
def test_float_bounds_reject_non_number(bound: str) -> None:
    spec: dict[str, Any] = {"type": "float", "low": 0.1, "high": 0.9}
    spec[bound] = "0.5"

    with pytest.raises(ValueError, match="must be a number"):
        _validate_param(spec)


def test_int_param_rejects_float_bound() -> None:
    with pytest.raises(ValueError, match="must be an int"):
        _validate_param({"type": "int", "low": 1.5, "high": 10})


def test_float_param_accepts_int_bounds() -> None:
    result = _validate_param({"type": "float", "low": 0, "high": 1})

    assert result["search_space"]["p"] == {"type": "float", "low": 0, "high": 1}


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "int", "low": 10, "high": 10},
        {"type": "int", "low": 10, "high": 1},
        {"type": "float", "low": 0.5, "high": 0.5},
    ],
)
def test_bounds_require_low_less_than_high(spec: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="'low' < 'high'"):
        _validate_param(spec)


@pytest.mark.parametrize("token", ["Infinity", "-Infinity", "NaN"])
def test_bounds_reject_non_finite(token: str) -> None:
    """`json.loads` parses the bare `Infinity`/`NaN` tokens into real floats, so
    a non-finite bound genuinely reaches the validator."""
    raw = json.dumps(_design(search_space={"p": {"type": "float", "low": 0.1, "high": 1.0}}))
    raw = raw.replace('"high": 1.0', f'"high": {token}')
    data = extract_json_object(raw, SPECIALIST)

    with pytest.raises(ValueError, match="finite"):
        _validate(data)


# -- log / step --


def test_log_true_requires_positive_low() -> None:
    with pytest.raises(ValueError, match="requires 'low' > 0"):
        _validate_param({"type": "float", "low": 0.0, "high": 1.0, "log": True})


def test_log_with_step_raises() -> None:
    """Optuna raises on log+step at `suggest_*` time — fail here instead, before
    the design is written to disk."""
    with pytest.raises(ValueError, match="step"):
        _validate_param({"type": "float", "low": 0.1, "high": 1.0, "log": True, "step": 0.1})


@pytest.mark.parametrize("log", ["true", 1, None])
def test_log_non_bool_raises(log: Any) -> None:
    with pytest.raises(ValueError, match="must be a boolean"):
        _validate_param({"type": "float", "low": 0.1, "high": 1.0, "log": log})


@pytest.mark.parametrize("step", [0, -1, 0.0])
def test_step_must_be_positive(step: Any) -> None:
    with pytest.raises(ValueError, match="greater than 0"):
        _validate_param({"type": "float", "low": 0.1, "high": 1.0, "step": step})


def test_step_rejects_bool() -> None:
    with pytest.raises(ValueError, match="boolean"):
        _validate_param({"type": "float", "low": 0.1, "high": 1.0, "step": True})


def test_step_rejects_non_number() -> None:
    with pytest.raises(ValueError, match="must be a number"):
        _validate_param({"type": "float", "low": 0.1, "high": 1.0, "step": "0.1"})


def test_int_step_must_be_int() -> None:
    with pytest.raises(ValueError, match="must be an int"):
        _validate_param({"type": "int", "low": 1, "high": 10, "step": 0.5})


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "int", "low": 1, "high": 2, "step": 5},
        {"type": "float", "low": 0.1, "high": 0.2, "step": 1.0},
    ],
)
def test_step_larger_than_range_raises(spec: dict[str, Any]) -> None:
    """Optuna does not reject this — `suggest_int(low=1, high=2, step=5)` returns
    the same value on every trial, silently burning the whole budget without
    tuning the parameter."""
    with pytest.raises(ValueError, match="collapse to a constant"):
        _validate_param(spec)


def test_step_exactly_equal_to_range_accepted() -> None:
    result = _validate_param({"type": "int", "low": 1, "high": 3, "step": 2})

    assert result["search_space"]["p"]["step"] == 2


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "float", "low": 0.1, "high": 0.3, "step": 0.2},
        {"type": "float", "low": 0.1, "high": 0.7, "step": 0.6},
        {"type": "float", "low": 1.1, "high": 2.2, "step": 1.1},
        {"type": "float", "low": 0.2, "high": 0.9, "step": 0.7},
    ],
)
def test_step_equal_to_range_accepted_despite_float_error(spec: dict[str, Any]) -> None:
    """Regression guard: `0.3 - 0.1` is `0.19999999999999998` in binary floating
    point, so a bare `step > high - low` rejects this perfectly legitimate
    two-value grid while `0.1/0.7/0.6` happens to pass — an input-dependent false
    rejection, on a node with no retry wrapper. Optuna itself accepts all four."""
    result = _validate_param(spec)

    assert result["search_space"]["p"]["step"] == spec["step"]


@pytest.mark.parametrize("bound", ["low", "high"])
def test_oversized_int_bound_raises_value_error_not_overflow_error(bound: str) -> None:
    """`math.isfinite(10**400)` raises `OverflowError`, which would escape the
    module's "always ValueError" contract that a critic-retry wrapper depends on."""
    spec: dict[str, Any] = {"type": "int", "low": 1, "high": 10}
    spec[bound] = 10**400

    with pytest.raises(ValueError, match="too large"):
        _validate_param(spec)


def test_unknown_inner_param_keys_dropped() -> None:
    result = _validate_param(
        {"type": "int", "low": 1, "high": 10, "distribution": "uniform", "q": 2}
    )

    assert result["search_space"]["p"] == {"type": "int", "low": 1, "high": 10}


# -- categorical choices --


@pytest.mark.parametrize("choices", [[], None, "gbdt", {}])
def test_categorical_choices_empty_raises(choices: Any) -> None:
    with pytest.raises(ValueError, match="choices"):
        _validate_param({"type": "categorical", "choices": choices})


def test_categorical_choices_duplicate_raises() -> None:
    with pytest.raises(ValueError, match="must not repeat"):
        _validate_param({"type": "categorical", "choices": ["gbdt", "dart", "gbdt"]})


@pytest.mark.parametrize("choices", [[1, True], [1, 1.0], [1.0, True], [1, 1.0, True]])
def test_categorical_choices_equal_values_are_duplicates(choices: list[Any]) -> None:
    """Optuna's `CategoricalDistribution` maps `1`, `1.0` and `True` to the same
    internal index, so a trial that trained on `True` is recorded as `1` and the
    winning configuration is no longer reproducible by `coder`. They are one
    choice, not several."""
    with pytest.raises(ValueError, match="must not repeat"):
        _validate_param({"type": "categorical", "choices": choices})


@pytest.mark.parametrize("value", [2**53 + 1, -(2**53) - 1, 10**400])
def test_categorical_choices_reject_inexact_int(value: int) -> None:
    """Python's arbitrary-precision ints serialize in full, but every IEEE-754
    consumer rounds them: `2**53 + 1` reads back as `2**53` and `10**400` reads
    back as `Infinity`/`null`. The same ±2**53 limit `_validate_numeric` applies
    to bounds has to apply here too."""
    with pytest.raises(ValueError, match="JSON scalars"):
        _validate_param({"type": "categorical", "choices": [1, value]})


def test_categorical_choices_accept_the_exact_int_boundary() -> None:
    result = _validate_param({"type": "categorical", "choices": [2**53, -(2**53)]})

    assert result["search_space"]["p"]["choices"] == [2**53, -(2**53)]


@pytest.mark.parametrize("token", ["Infinity", "-Infinity", "NaN"])
def test_categorical_choices_reject_non_finite(token: str) -> None:
    """`json.dump`'s default `allow_nan=True` would emit these tokens verbatim,
    producing a `design.json` that is not valid RFC-8259 JSON."""
    raw = json.dumps(_design(search_space={"p": {"type": "categorical", "choices": [1]}}))
    raw = raw.replace('"choices": [1]', f'"choices": [{token}]')

    with pytest.raises(ValueError, match="finite"):
        _validate(extract_json_object(raw, SPECIALIST))


@pytest.mark.parametrize("choice", [{"nested": 1}, ["nested"]])
def test_categorical_choices_non_scalar_raises(choice: Any) -> None:
    with pytest.raises(ValueError, match="JSON scalars"):
        _validate_param({"type": "categorical", "choices": ["gbdt", choice]})


def test_categorical_choices_valid_accepted() -> None:
    result = _validate_param({"type": "categorical", "choices": ["gbdt", "dart", None, 3, 1.5]})

    assert result["search_space"]["p"] == {
        "type": "categorical",
        "choices": ["gbdt", "dart", None, 3, 1.5],
    }


# -- fixed_params --


def test_fixed_params_required_even_when_empty() -> None:
    data = _design()
    del data["fixed_params"]

    with pytest.raises(ValueError, match="fixed_params"):
        _validate(data)

    assert _validate(_design(fixed_params={}))["fixed_params"] == {}


@pytest.mark.parametrize("value", [[], "none", None, 3])
def test_fixed_params_not_a_dict_raises(value: Any) -> None:
    with pytest.raises(ValueError, match="fixed_params"):
        _validate(_design(fixed_params=value))


def test_fixed_params_nested_object_value_raises() -> None:
    with pytest.raises(ValueError, match="nested object"):
        _validate(_design(fixed_params={"tree_params": {"max_depth": 5}}))


def test_fixed_params_flat_list_value_accepted() -> None:
    result = _validate(_design(fixed_params={"metric": ["auc", "binary_logloss"]}))

    assert result["fixed_params"] == {"metric": ["auc", "binary_logloss"]}


@pytest.mark.parametrize("value", [2**53 + 1, -(2**53) - 1, 10**400])
def test_fixed_params_reject_inexact_int_scalar(value: int) -> None:
    with pytest.raises(ValueError, match="JSON scalar"):
        _validate(_design(fixed_params={"scale_pos_weight": value}))


@pytest.mark.parametrize("value", [2**53 + 1, 10**400])
def test_fixed_params_reject_inexact_int_in_a_list(value: int) -> None:
    with pytest.raises(ValueError, match="JSON scalar"):
        _validate(_design(fixed_params={"class_weights": [1, value]}))


def test_fixed_params_accept_the_exact_int_boundary() -> None:
    result = _validate(_design(fixed_params={"seed": 2**53}))

    assert result["fixed_params"] == {"seed": 2**53}


@pytest.mark.parametrize("token", ["Infinity", "-Infinity", "NaN"])
def test_fixed_params_reject_non_finite(token: str) -> None:
    raw = json.dumps(_design(fixed_params={"scale_pos_weight": 1}))
    raw = raw.replace('"scale_pos_weight": 1', f'"scale_pos_weight": {token}')

    with pytest.raises(ValueError, match="finite"):
        _validate(extract_json_object(raw, SPECIALIST))


# -- parameter names (they become Python keyword arguments in generated code) --


@pytest.mark.parametrize(
    "name",
    [
        'x", exec("import os"), "y',
        "max_depth\nimport os",
        "max depth",
        "max-depth",
        "2fast",
        "",
        "n" * 65,
        "learning_rate(0.1)",
        "path\\to\\param",
    ],
)
@pytest.mark.parametrize("field", ["search_space", "fixed_params"])
def test_invalid_parameter_name_raises(field: str, name: str) -> None:
    """`coder` (T-029) turns these keys into keyword arguments in a script
    `code_executor` runs, so anything that is not a plain identifier is refused
    before it can reach disk."""
    if field == "search_space":
        data = _design(search_space={name: {"type": "int", "low": 1, "high": 10}})
    else:
        data = _design(fixed_params={name: 1})

    with pytest.raises(ValueError, match="not a usable parameter name"):
        _validate(data)


@pytest.mark.parametrize("name", ["max_depth", "_private", "L2", "n" * 64, "colsample_bytree"])
def test_valid_parameter_names_accepted(name: str) -> None:
    result = _validate(_design(fixed_params={name: 1}))

    assert result["fixed_params"] == {name: 1}


# -- preprocessing --


def test_preprocessing_required_even_when_empty() -> None:
    data = _design()
    del data["preprocessing"]

    with pytest.raises(ValueError, match="preprocessing"):
        _validate(data)

    assert _validate(_design(preprocessing=[]))["preprocessing"] == []


@pytest.mark.parametrize("value", [{}, "no_scaling_required", None])
def test_preprocessing_not_a_list_raises(value: Any) -> None:
    with pytest.raises(ValueError, match="preprocessing"):
        _validate(_design(preprocessing=value))


@pytest.mark.parametrize("item", [1, None, "", "   ", {"step": "scale"}])
def test_preprocessing_non_string_item_raises(item: Any) -> None:
    with pytest.raises(ValueError, match=r"preprocessing\[0\]"):
        _validate(_design(preprocessing=[item]))


@pytest.mark.parametrize(
    "item",
    [
        "StratifiedKFold(n_splits=3, shuffle=True)",
        "train_test_split(X, y, test_size=0.2)",
        "Scale the numeric columns with a StandardScaler.",
        "NativeCategoricalHandling",
        "no-scaling-required",
        "s" * 65,
    ],
)
def test_preprocessing_code_shaped_entry_raises(item: str) -> None:
    """A `preprocessing` entry names a step, it never expresses one. Free-form
    strings let a call-shaped CV redefinition (`StratifiedKFold(n_splits=3, ...)`)
    slip past the forbidden-*key* guard by hiding in a list value."""
    with pytest.raises(ValueError, match=r"preprocessing\[0\]"):
        _validate(_design(preprocessing=[item]))


@pytest.mark.parametrize(
    "item",
    [
        "native_categorical_handling",
        "no_scaling_required",
        "label_encode_for_xgboost",
        "missing_values_handled_natively",
    ],
)
def test_preprocessing_token_entries_accepted(item: str) -> None:
    result = _validate(_design(preprocessing=[item]))

    assert result["preprocessing"] == [item]


# -- rationale --


@pytest.mark.parametrize("value", [None, "", "   ", 42, ["because"]])
def test_rationale_required_non_empty(value: Any) -> None:
    with pytest.raises(ValueError, match="rationale"):
        _validate(_design(rationale=value))


# -- name collisions --


def test_param_in_both_search_space_and_fixed_params_raises() -> None:
    data = _design(fixed_params={"n_estimators": 500, "objective": "binary"})

    with pytest.raises(ValueError, match="n_estimators"):
        _validate(data)


# -- forbidden CV keys (Done when: "the design does not redefine CV") --


@pytest.mark.parametrize("key", sorted(FORBIDDEN_CV_KEYS))
def test_forbidden_cv_key_at_top_level_raises(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        _validate(_design(**{key: "anything"}))


@pytest.mark.parametrize("key", sorted(FORBIDDEN_CV_KEYS))
def test_forbidden_cv_key_in_search_space_raises(key: str) -> None:
    search_space = {**VALID_DESIGN["search_space"], key: {"type": "int", "low": 2, "high": 10}}

    with pytest.raises(ValueError, match=f"{key}.*search_space"):
        _validate(_design(search_space=search_space))


@pytest.mark.parametrize("key", sorted(FORBIDDEN_CV_KEYS))
def test_forbidden_cv_key_in_fixed_params_raises(key: str) -> None:
    with pytest.raises(ValueError, match=f"{key}.*fixed_params"):
        _validate(_design(fixed_params={key: 5}))


def test_forbidden_cv_key_is_matched_by_exact_name_not_substring() -> None:
    """`n_splits` is forbidden; `min_child_samples_n_splits_hint` merely contains
    it and must pass — the rule is exact key equality."""
    result = _validate(
        _design(fixed_params={"min_child_samples_n_splits_hint": 20, "shuffle_buffer": 100})
    )

    assert result["fixed_params"] == {
        "min_child_samples_n_splits_hint": 20,
        "shuffle_buffer": 100,
    }


# -- read_fold_summary (real WorkspaceManager, real tmp_path files) --


def _workspace_state(tmp_path: Path) -> tuple[WorkspaceManager, LabState]:
    workspace = WorkspaceManager(str(tmp_path))
    state = new_state("comp", str(tmp_path))
    return workspace, state


def test_read_fold_summary_omits_fold_indices(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    written = workspace.write_json(
        "validation/fold_config.json",
        {
            "strategy": "stratified_kfold",
            "n_folds": 5,
            "seed": 42,
            "fold_indices": [{"train": [111111], "val": [222222]}],
        },
    )
    state["validation_config_path"] = written

    summary = read_fold_summary(state, workspace)

    assert "fold_indices" not in summary
    assert "222222" not in summary
    assert json.loads(summary) == {"strategy": "stratified_kfold", "n_folds": 5, "seed": 42}


def test_read_fold_summary_degrades_on_unset_path(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)

    assert read_fold_summary(state, workspace) == _NOT_AVAILABLE


def test_read_fold_summary_degrades_on_oserror(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    state["validation_config_path"] = "validation/fold_config.json"

    summary = read_fold_summary(state, workspace)

    assert summary.startswith("(unable to read frozen fold config at")


def test_read_fold_summary_degrades_on_non_dict_json(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    workspace.write_text("validation/fold_config.json", "[1, 2, 3]")
    state["validation_config_path"] = "validation/fold_config.json"

    assert read_fold_summary(state, workspace) == _NOT_AVAILABLE


@pytest.mark.parametrize("content", ['{"strategy": "kfold", "n_fol', "", "   \n"])
def test_read_fold_summary_degrades_on_malformed_json(tmp_path: Path, content: str) -> None:
    """A truncated or empty fold config raises `json.JSONDecodeError` out of
    `read_json`; it must degrade, not abort the whole graph run."""
    workspace, state = _workspace_state(tmp_path)
    workspace.write_text("validation/fold_config.json", content)
    state["validation_config_path"] = "validation/fold_config.json"

    assert read_fold_summary(state, workspace).startswith("(unable to read")


def test_read_fold_summary_degrades_on_invalid_utf8(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    (tmp_path / "validation").mkdir(parents=True, exist_ok=True)
    (tmp_path / "validation" / "fold_config.json").write_bytes(b'{"strategy": "\xff\xfe"}')
    state["validation_config_path"] = "validation/fold_config.json"

    assert read_fold_summary(state, workspace).startswith("(unable to read")


def test_read_fold_summary_degrades_on_path_outside_the_workspace(tmp_path: Path) -> None:
    """A resumed run can carry an absolute path recorded against a workspace root
    that has since moved — `relative_to_workspace` raises `ValueError` there."""
    workspace, state = _workspace_state(tmp_path)
    state["validation_config_path"] = "/elsewhere/validation/fold_config.json"

    assert read_fold_summary(state, workspace).startswith("(unable to read")


def test_read_fold_summary_degrades_on_traversal_path(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    state["validation_config_path"] = "../../etc/passwd"

    assert read_fold_summary(state, workspace).startswith("(unable to read")


def test_read_fold_summary_degrades_on_pathological_nesting(tmp_path: Path) -> None:
    """A deeply nested payload exhausts the interpreter stack inside `json.loads`
    (and again inside `json.dumps` on the way out). `RecursionError` is a
    `RuntimeError`, so neither `OSError` nor `ValueError` catches it — but the
    docstring promises this never raises."""
    workspace, state = _workspace_state(tmp_path)
    depth = 100_000
    workspace.write_text("validation/fold_config.json", "[" * depth + "]" * depth)
    state["validation_config_path"] = "validation/fold_config.json"

    assert read_fold_summary(state, workspace).startswith("(unable to read")


def test_read_fold_summary_degrades_on_non_string_path(tmp_path: Path) -> None:
    """`LabState` types this as `str`, but LangGraph does not enforce the
    TypedDict at runtime — a non-string would raise `TypeError` out of `Path()`,
    which is not in the caught set."""
    workspace, state = _workspace_state(tmp_path)
    state["validation_config_path"] = 42  # type: ignore[typeddict-item]

    assert read_fold_summary(state, workspace) == _NOT_AVAILABLE


# -- read_solution_plan (real WorkspaceManager, real tmp_path files) --


def test_read_solution_plan_degrades_on_unset_path(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)

    assert read_solution_plan(state, workspace) == "(solution plan not yet available)"


def test_read_solution_plan_degrades_on_oserror(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    state["solution_plan_path"] = "design/iteration_0/solution_plan.json"

    plan = read_solution_plan(state, workspace)

    assert plan.startswith("(unable to read solution plan at")


@pytest.mark.parametrize("content", ['{"model_families": ["xgb', "", "   \n"])
def test_read_solution_plan_degrades_on_malformed_json(tmp_path: Path, content: str) -> None:
    """A truncated or empty solution plan raises `json.JSONDecodeError` out of
    `read_json`; it must degrade, not abort the whole graph run."""
    workspace, state = _workspace_state(tmp_path)
    workspace.write_text("design/iteration_0/solution_plan.json", content)
    state["solution_plan_path"] = "design/iteration_0/solution_plan.json"

    assert read_solution_plan(state, workspace).startswith("(unable to read")


def test_read_solution_plan_degrades_on_invalid_utf8(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    (tmp_path / "design" / "iteration_0").mkdir(parents=True, exist_ok=True)
    (tmp_path / "design" / "iteration_0" / "solution_plan.json").write_bytes(
        b'{"model_families": "\xff\xfe"}'
    )
    state["solution_plan_path"] = "design/iteration_0/solution_plan.json"

    assert read_solution_plan(state, workspace).startswith("(unable to read")


def test_read_solution_plan_degrades_on_path_outside_the_workspace(tmp_path: Path) -> None:
    """A resumed run can carry an absolute path recorded against a workspace root
    that has since moved — `relative_to_workspace` raises `ValueError` there."""
    workspace, state = _workspace_state(tmp_path)
    state["solution_plan_path"] = "/elsewhere/design/iteration_0/solution_plan.json"

    assert read_solution_plan(state, workspace).startswith("(unable to read")


def test_read_solution_plan_degrades_on_traversal_path(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    state["solution_plan_path"] = "../../etc/passwd"

    assert read_solution_plan(state, workspace).startswith("(unable to read")


def test_read_solution_plan_degrades_on_pathological_nesting(tmp_path: Path) -> None:
    """A deeply nested payload exhausts the interpreter stack inside `json.loads`
    (and again inside `json.dumps` on the way out). `RecursionError` is a
    `RuntimeError`, so neither `OSError` nor `ValueError` catches it — but the
    docstring promises this never raises."""
    workspace, state = _workspace_state(tmp_path)
    depth = 100_000
    workspace.write_text("design/iteration_0/solution_plan.json", "[" * depth + "]" * depth)
    state["solution_plan_path"] = "design/iteration_0/solution_plan.json"

    assert read_solution_plan(state, workspace).startswith("(unable to read")


def test_read_solution_plan_degrades_on_non_string_path(tmp_path: Path) -> None:
    """`LabState` types this as `str`, but LangGraph does not enforce the
    TypedDict at runtime — a non-string would raise `TypeError` out of `Path()`,
    which is not in the caught set."""
    workspace, state = _workspace_state(tmp_path)
    state["solution_plan_path"] = 42  # type: ignore[typeddict-item]

    assert read_solution_plan(state, workspace) == "(solution plan not yet available)"


def test_read_solution_plan_happy_path_returns_pretty_printed_json(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    written = workspace.write_json(
        "design/iteration_0/solution_plan.json",
        {"model_families": ["tfidf_linear"], "order": ["tfidf_linear"]},
    )
    state["solution_plan_path"] = written

    plan = read_solution_plan(state, workspace)

    assert json.loads(plan) == {"model_families": ["tfidf_linear"], "order": ["tfidf_linear"]}


# -- resolve_feature_spec_ref --


def test_resolve_feature_spec_ref_relativizes_absolute(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    state["feature_spec_path"] = str(
        workspace.workspace_path / "design" / "iteration_0" / "feature_spec.json"
    )

    ref = resolve_feature_spec_ref(state, workspace)

    assert ref == "design/iteration_0/feature_spec.json"


def test_resolve_feature_spec_ref_passes_through_relative(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    state["feature_spec_path"] = "design/iteration_1/feature_spec.json"

    ref = resolve_feature_spec_ref(state, workspace)

    assert ref == "design/iteration_1/feature_spec.json"


def test_resolve_feature_spec_ref_falls_back_to_iteration_pattern(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    state["current_iteration"] = 3

    ref = resolve_feature_spec_ref(state, workspace)

    assert ref == "design/iteration_3/feature_spec.json"


def test_resolve_feature_spec_ref_falls_back_on_foreign_absolute_path(tmp_path: Path) -> None:
    """Resuming a checkpointed run after the workspace has been moved, renamed or
    bind-mounted elsewhere leaves an absolute path that is not under the current
    root — `relative_to_workspace` raises there, and the fallback must absorb it
    rather than aborting the node."""
    workspace, state = _workspace_state(tmp_path)
    state["current_iteration"] = 2
    state["feature_spec_path"] = "/some/other/workspace/design/iteration_2/feature_spec.json"

    assert resolve_feature_spec_ref(state, workspace) == "design/iteration_2/feature_spec.json"


@pytest.mark.parametrize(
    "stored", ["../../etc/passwd", "design/../../../etc/passwd", "../feature_spec.json"]
)
def test_resolve_feature_spec_ref_falls_back_on_traversal_path(tmp_path: Path, stored: str) -> None:
    """A `..` component must never be written into `design.json`: `coder` resolves
    this pointer relative to the workspace root."""
    workspace, state = _workspace_state(tmp_path)
    state["feature_spec_path"] = stored

    assert resolve_feature_spec_ref(state, workspace) == "design/iteration_0/feature_spec.json"


def test_resolve_feature_spec_ref_falls_back_on_non_string_path(tmp_path: Path) -> None:
    workspace, state = _workspace_state(tmp_path)
    state["feature_spec_path"] = 42  # type: ignore[typeddict-item]

    assert resolve_feature_spec_ref(state, workspace) == "design/iteration_0/feature_spec.json"
