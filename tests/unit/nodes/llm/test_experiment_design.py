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
    resolve_feature_spec_ref,
    strip_outer_fence,
    validate_experiment_design,
)
from src.state import LabState, new_state
from src.workspace.workspace_manager import WorkspaceManager

SPECIALIST = "classical_ml_specialist"
FAMILIES: dict[str, tuple[str, ...]] = {
    "xgboost": ("xgboost", "xgb"),
    "lightgbm": ("lightgbm", "lgbm", "lgb", "light gbm"),
    "catboost": ("catboost", "cat boost"),
    "extra_trees": ("extra trees", "extratrees", "extremely randomized trees"),
}
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


# -- strip_outer_fence / extract_json_object --


def test_strip_outer_fence_passes_through_unfenced() -> None:
    assert strip_outer_fence('{"a": 1}', SPECIALIST) == '{"a": 1}'


def test_strip_outer_fence_strips_json_fence() -> None:
    assert strip_outer_fence('```json\n{"a": 1}\n```', SPECIALIST) == '{"a": 1}'


def test_strip_outer_fence_unclosed_raises() -> None:
    with pytest.raises(ValueError, match=SPECIALIST):
        strip_outer_fence('```json\n{"a": 1}', SPECIALIST)


def test_strip_outer_fence_single_line_fence_raises() -> None:
    with pytest.raises(ValueError, match="fence has no content"):
        strip_outer_fence("``````", SPECIALIST)


def test_extract_json_object_rejects_array() -> None:
    with pytest.raises(ValueError, match="must be a JSON object"):
        extract_json_object('[{"a": 1}]', SPECIALIST)


def test_extract_json_object_rejects_invalid_json() -> None:
    with pytest.raises(ValueError, match=SPECIALIST):
        extract_json_object("not json at all {", SPECIALIST)


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("xgb", "xgboost"),
        ("XGBoost", "xgboost"),
        ("LGBM", "lightgbm"),
        ("light-gbm", "lightgbm"),
        ("CatBoost", "catboost"),
        ("extra-trees", "extra_trees"),
        ("ExtraTrees", "extra_trees"),
        ("extremely randomized trees", "extra_trees"),
    ],
)
def test_model_family_alias_normalizes(raw: str, expected: str) -> None:
    assert normalize_model_family(raw, FAMILIES, SPECIALIST) == expected


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


def test_categorical_choices_int_and_bool_are_not_conflated() -> None:
    """Python treats `1 == True` and hashes them identically — the dedupe key
    includes the type name so a legitimate `[1, true]` choice list survives."""
    result = _validate_param({"type": "categorical", "choices": [1, True]})

    assert result["search_space"]["p"]["choices"] == [1, True]


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
