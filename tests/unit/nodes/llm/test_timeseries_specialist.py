"""Unit tests for src/nodes/llm/timeseries_specialist.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at both import locations, matching
`test_classical_ml_specialist.py`'s convention). No network calls, no real
filesystem writes.

The `design.json` schema rules themselves are covered exhaustively in
`test_experiment_design.py`; the cases here are the node's own contract —
output path, injected fields, prompt assembly, its own temporal model-family
table, and the three temporal-specific rules that live in the prompt rather
than in the validator (no self-gate on temporal structure, past-only features,
column identity from the feature spec).

Scope note on the task's "never uses future data" criterion: the enforceable
half is asserted here (pipeline-injected `cv_strategy_ref` plus the
`FORBIDDEN_CV_KEYS` rejection, including the `TimeSeriesSplit`-shaped keys a
temporal design reaches for most naturally). Leakage itself is not machine
checkable from a flat list of step names, so it is prompt-level guidance —
`test_every_preprocessing_token_the_prompt_recommends_satisfies_the_validator`
and the prompt/example pins are what keep that guidance honest.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings
from src.nodes.llm._experiment_design import (
    _PREPROCESSING_STEP_RE,
    FORBIDDEN_CV_KEYS,
)
from src.nodes.llm.timeseries_specialist import (
    _MODEL_FAMILIES,
    TimeseriesSpecialistNode,
)
from src.state import new_state

CANONICAL_FAMILIES = {
    "arima",
    "prophet",
    "exponential_smoothing",
    "gradient_boosting_lags",
    "linear_lags",
}

# The worked example from `config/prompts/timeseries_specialist/v1.md`.
# `test_prompt_json_example_matches_this_modules_payload` pins the two together,
# so the mocked responses here stay the shape the prompt actually asks for.
VALID_DESIGN: dict[str, Any] = {
    "model_family": "gradient_boosting_lags",
    "search_space": {
        "n_lags": {"type": "int", "low": 1, "high": 28},
        "rolling_window": {"type": "int", "low": 3, "high": 30},
        "n_estimators": {"type": "int", "low": 100, "high": 1000},
        "learning_rate": {"type": "float", "low": 0.01, "high": 0.3, "log": True},
        "max_depth": {"type": "int", "low": 3, "high": 10},
    },
    "fixed_params": {"objective": "regression"},
    "preprocessing": ["lag_features_past_only", "rolling_mean_past_only", "calendar_features"],
    "rationale": (
        "The frozen fold summary reports no time-aware split, so a gradient-boosted model over "
        "past-only lag and rolling features is the safest design: it degrades gracefully if the "
        "temporal signal is weak, and the lag window is itself tuned rather than assumed. "
        "Seasonality is left to calendar features instead of an explicit seasonal order."
    ),
}
RESPONSE_CONTENT = json.dumps(VALID_DESIGN)
SOLUTION_PLAN = {"model_families": ["prophet"], "order": ["prophet"]}
# `stratified_kfold` on purpose, not a time-series split: the frozen strategy is a
# read-only fact of the run and this node may not change it (see
# `test_build_messages_omits_fold_indices_but_keeps_non_time_aware_strategy`).
FOLD_CONFIG = {
    "strategy": "stratified_kfold",
    "n_folds": 5,
    "seed": 42,
    "fold_indices": [{"train": [111111], "val": [222222]}],
}

# (alias the LLM might write, canonical family it must resolve to) for every alias
# in the real table. Catches a renamed canonical key, a typo'd alias, and an alias
# that is unreachable after normalization.
#
# It CANNOT catch a cross-family collision: each case feeds exactly one alias, and a
# collision by definition needs two aliases from different families co-occurring in
# one string, which single-alias parametrization can never construct. That blind spot
# is why a bare `"linear"` alias shipped past 39 green tests while making "Holt's
# linear trend method" and "Prophet (growth=linear)" hard-abort the phase.
# `_MULTIWORD_PHRASING_CASES` below is the guard that actually covers collisions.
_ALIAS_CASES = [(alias, family) for family, aliases in _MODEL_FAMILIES.items() for alias in aliases]

# Realistic full answers, not bare aliases: the phrasings an LLM actually writes, each
# mapped to the ONE family it must resolve to. Every entry co-occurs with vocabulary
# that is adjacent to another family — a trend word ("linear"), a base-learner mention,
# a hyperparameter spelling — so a future over-broad alias makes one of these ambiguous
# instead of passing unnoticed. The CamelCase block additionally pins the concatenated
# spellings that normalization cannot reach from their spaced twins (it collapses
# `-`/`_` to a space but never splits CamelCase).
_MULTIWORD_PHRASING_CASES: list[tuple[str, str]] = [
    # "linear" as a TREND word, never a family word — each of these was a hard
    # `ValueError("ambiguous")` while a bare "linear" alias existed.
    ("Holt's linear trend method", "exponential_smoothing"),
    ("damped linear trend exponential smoothing", "exponential_smoothing"),
    ("ETS with linear trend and additive seasonality", "exponential_smoothing"),
    ("Prophet (growth=linear)", "prophet"),
    ("Prophet with piecewise linear trend", "prophet"),
    ("ARIMA with linear trend", "arima"),
    ("SARIMAX with a linear time trend", "arima"),
    ("LightGBM linear_tree", "gradient_boosting_lags"),
    ("gradient boosting with linear base learners", "gradient_boosting_lags"),
    # "linear" as a genuine family word.
    ("linear regression over lag features", "linear_lags"),
    ("Ridge regression on lag features", "linear_lags"),
    # Concatenated / CamelCase library and token spellings.
    ("ExponentialSmoothing", "exponential_smoothing"),
    ("HoltWinters", "exponential_smoothing"),
    ("GradientBoostingRegressor", "gradient_boosting_lags"),
    ("HistGradientBoostingRegressor", "gradient_boosting_lags"),
    ("XGBRegressor", "gradient_boosting_lags"),
    ("LGBMRegressor", "gradient_boosting_lags"),
    ("GradientBoostingLags", "gradient_boosting_lags"),
    ("LinearLags", "linear_lags"),
    ("LinearRegression", "linear_lags"),
    ("AutoARIMA", "arima"),
    # Other realistic multi-word answers.
    ("seasonal ARIMA with exogenous regressors", "arima"),
    ("Prophet with yearly seasonality", "prophet"),
    ("LightGBM over lagged targets", "gradient_boosting_lags"),
]

# Bagged-tree answers, which this table deliberately does NOT alias — see the comment
# above `_MODEL_FAMILIES["gradient_boosting_lags"]`. They must fail loudly rather than
# resolve to a boosting family whose constructor would reject their hyperparameters.
_BAGGING_ANSWERS = [
    "random forest",
    "RandomForestRegressor",
    "extra trees",
    "ExtraTreesRegressor",
    "decision tree",
    "tree ensemble",
]

# Cross-validation keys a temporal design reaches for most naturally. `n_splits` and
# `test_size` are genuinely `TimeSeriesSplit` arguments; `shuffle` and `validation` are
# NOT (`TimeSeriesSplit` takes `n_splits`/`max_train_size`/`test_size`/`gap`, and the
# absence of `shuffle` is its defining property) — they come from `KFold`/
# `train_test_split` habits. All four are in `FORBIDDEN_CV_KEYS`; the values are the
# shapes each would really be written with.
_TIMESERIES_SPLIT_PARAMS: list[tuple[str, Any]] = [
    ("n_splits", 5),
    ("test_size", 0.2),
    ("shuffle", False),
    ("validation", 0.1),
]

# Backticked strings in the prompt's § Preprocessing scope section that are
# deliberately NOT step-name recommendations: the shape regex itself, the two call
# forms the section shows as *rejected*, and a filename. Everything else backticked
# in that section is read as a recommended token and must satisfy
# `_PREPROCESSING_STEP_RE`. Inverted (allowlist) rather than regex-extracted, so a
# new bad recommendation fails by default — see the same guard in
# `test_deep_learning_specialist.py`.
_PROMPT_NON_TOKEN_MENTIONS = frozenset(
    {
        "^[a-z][a-z0-9_]{0,63}$",
        "TimeSeriesSplit(n_splits=5)",
        "df.rolling(7).mean()",
        "feature_spec.json",
    }
)


def _design(**overrides: Any) -> str:
    data = copy.deepcopy(VALID_DESIGN)
    data.update(overrides)
    return json.dumps(data)


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


def _first_json_block(prompt: str) -> dict[str, Any]:
    """Parse the first ```json fenced block of the prompt — the worked example.

    The rejected-shape snippets in the prompt are deliberately written as inline
    code spans rather than fenced blocks, precisely so this lookup finds the one
    payload that is supposed to be valid.
    """
    marker = "```json\n"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("\n```", start)
    parsed = json.loads(prompt[start:end])
    assert isinstance(parsed, dict)
    return parsed


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=RESPONSE_CONTENT)
    return llm


@pytest.fixture
def patched_llm_factory(mock_llm: MagicMock):
    with patch("src.nodes.llm.base.LLMFactory") as mock_factory:
        mock_factory.get.return_value = mock_llm
        yield mock_factory


@pytest.fixture
def patched_settings():
    with patch("src.nodes.llm.base.Settings") as mock_settings_cls:
        mock_settings_cls.load.return_value = _make_settings()
        yield mock_settings_cls


@pytest.fixture
def mock_workspace_manager():
    """Patched at both import locations — `src.nodes.llm.base` (the base class's
    own `__call__`, which writes the output) and
    `src.nodes.llm.timeseries_specialist` (the node's own instance, built in
    `_build_messages` to read the upstream artifacts) — resolving to the same mock
    instance, since neither call site knows about the other's."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.return_value = SOLUTION_PLAN
    instance.write_json.return_value = "/workspace/experiments/exp_0/design.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.timeseries_specialist.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> Any:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    state["solution_plan_path"] = "design/iteration_0/solution_plan.json"
    state["validation_config_path"] = "validation/fold_config.json"
    state["feature_spec_path"] = "design/iteration_0/feature_spec.json"
    return state


def _written_design(workspace_instance: MagicMock) -> dict[str, Any]:
    args, _ = workspace_instance.write_json.call_args
    return args[1]


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("timeseries_specialist")

    assert config.name == "timeseries_specialist"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "experiments/exp_{iteration}/design.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("timeseries_specialist", "v1")
    assert prompt.strip() != ""
    # The *first line* specifically, not merely "somewhere in the prompt":
    # `tests/fixtures/graph_mocks.py` routes mocked responses by matching this
    # exact header, so a demoted or reworded first line silently mis-routes the
    # integration mocks to another node's payload.
    assert prompt.splitlines()[0] == "# System prompt — timeseries_specialist"


# -- __call__ behavior --


def test_call_writes_design_with_search_space_and_temporal_model_family(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "experiments/exp_0/design.json"
    assert "search_space" in args[1]
    assert args[1]["search_space"]["n_lags"] == {"type": "int", "low": 1, "high": 28}
    assert args[1]["model_family"] == "gradient_boosting_lags"


def test_written_design_contains_temporal_preprocessing(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """The design must carry the temporal features it was routed here to design,
    not just any search space."""
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert "lag_features_past_only" in written["preprocessing"]
    assert "rolling_window" in written["search_space"]


def test_output_path_uses_current_iteration(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state(current_iteration=3))

    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "experiments/exp_3/design.json"


def test_delta_adds_no_new_labstate_field(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`coder` (T-029) reads `experiments/exp_{iteration}/design.json` from its
    well-known path, so this node must not introduce a `LabState` field —
    `src/state.py` is a protected contract."""
    node = TimeseriesSpecialistNode()

    delta = node(_build_state())

    assert set(delta) == {"messages"}


# -- frozen folds (Done when: "the design references the frozen folds") --


def test_written_design_references_frozen_folds(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(cv_strategy_ref="my_own_folds.json"))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["cv_strategy_ref"] == "validation/fold_config.json"


def test_written_design_references_frozen_folds_when_llm_omits_the_key(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["cv_strategy_ref"] == "validation/fold_config.json"


@pytest.mark.parametrize("key", sorted(FORBIDDEN_CV_KEYS))
def test_llm_cv_key_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, key: str
) -> None:
    """A design that tries to redefine cross-validation is rejected outright and
    nothing is written — the folds are frozen (CLAUDE.md invariant #1)."""
    mock_llm.invoke.return_value = AIMessage(content=_design(**{key: "whatever"}))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match=key):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


@pytest.mark.parametrize(("key", "value"), _TIMESERIES_SPLIT_PARAMS)
def test_timeseries_split_params_in_fixed_params_are_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    key: str,
    value: Any,
) -> None:
    """The shape a temporal design most naturally reaches for: pinning
    `TimeSeriesSplit`'s own arguments as if they were model hyperparameters. That
    is a CV redefinition wherever it appears, so `fixed_params` is rejected exactly
    like the top level."""
    mock_llm.invoke.return_value = AIMessage(content=_design(fixed_params={key: value}))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match=key):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


# -- injected fields --


def test_specialist_field_injected_not_taken_from_llm(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(specialist="nlp_specialist"))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["specialist"] == "timeseries_specialist"


def test_feature_spec_ref_relativized_from_state(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """An absolute host path baked into `design.json` breaks inside
    `code_executor`'s subprocess and inside the container, where the workspace is
    bind-mounted elsewhere."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["feature_spec_path"] = "/workspace/design/iteration_0/feature_spec.json"
    node = TimeseriesSpecialistNode()

    node(state)

    assert (
        _written_design(workspace_instance)["feature_spec_ref"]
        == "design/iteration_0/feature_spec.json"
    )


def test_feature_spec_ref_falls_back_when_unset(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    state = _build_state(current_iteration=2)
    state["feature_spec_path"] = ""
    node = TimeseriesSpecialistNode()

    node(state)

    assert (
        _written_design(workspace_instance)["feature_spec_ref"]
        == "design/iteration_2/feature_spec.json"
    )


def test_n_trials_from_llm_not_written(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """The trial budget lives in `config/settings.yaml`'s `optuna:` block, never
    in a per-experiment design."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(n_trials=500, early_stopping_patience=99)
    )
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert "n_trials" not in written
    assert "early_stopping_patience" not in written


# -- model-family table integrity --


def test_model_family_table_is_exactly_the_five_families() -> None:
    """Pins the human-checkpoint decision: five canonical families, no more."""
    assert set(_MODEL_FAMILIES) == CANONICAL_FAMILIES


@pytest.mark.parametrize("family", sorted(CANONICAL_FAMILIES))
def test_every_canonical_key_resolves_to_itself(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    family: str,
) -> None:
    """The prompt tells the LLM to answer with the bare canonical token, so each
    one must round-trip through the alias table unchanged."""
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=family))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == family


@pytest.mark.parametrize(("alias", "expected_family"), _ALIAS_CASES)
def test_every_production_alias_resolves_to_its_family(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    alias: str,
    expected_family: str,
) -> None:
    """`coder` (T-029) dispatches on the written `model_family`, so every alias in
    the real table must canonicalize rather than pass through.

    Scope, precisely: this catches an alias that is unreachable after normalization
    (e.g. a concatenated spelling listed only in its spaced form) and a renamed
    canonical key. It does NOT catch cross-family collisions — one alias per case
    cannot produce one — see `_ALIAS_CASES` and
    `test_realistic_multiword_phrasings_resolve_to_exactly_one_family`.
    """
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=alias))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == expected_family


@pytest.mark.parametrize(("raw", "expected_family"), _MULTIWORD_PHRASING_CASES)
def test_realistic_multiword_phrasings_resolve_to_exactly_one_family(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    raw: str,
    expected_family: str,
) -> None:
    """The collision guard the per-alias parametrization structurally cannot be.

    A cross-family collision only appears when two families' vocabulary co-occur in
    one real answer, so these cases feed whole phrasings rather than bare aliases.
    Every "linear" case here was a hard `ValueError("ambiguous")` — an aborted phase
    with zero artifacts, on a node with no retry wrapper — while the table carried a
    bare `"linear"` alias, even though "Holt's linear trend method" is the textbook
    name for an `exponential_smoothing` model and `growth="linear"` is Prophet's own
    default. An over-broad alias added later fails here instead of in production.
    """
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=raw))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == expected_family


@pytest.mark.parametrize("raw", _BAGGING_ANSWERS)
def test_bagged_tree_answers_are_rejected_rather_than_resolved_to_boosting(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, raw: str
) -> None:
    """Failing loudly beats misdispatching silently.

    While `random forest`/`extra trees`/`decision tree`/`tree ensemble` were aliased
    to `gradient_boosting_lags`, a coherent RandomForest design — `bootstrap`,
    `oob_score`, and a rationale arguing for bagging — validated and was written with
    `model_family: "gradient_boosting_lags"` and its RF hyperparameters intact, so
    `coder` (T-029) would build a boosting model, be handed `bootstrap=`/`oob_score=`
    and die on a constructor `TypeError`, from a `design.json` contradicting its own
    `rationale`. Unaliased, the same answer raises "not a supported model family":
    attributable and recoverable. Same principle `deep_learning_specialist` states.
    """
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=raw))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match="not a supported model family"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


def test_bagged_tree_design_does_not_write_a_boosting_family(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """The end-to-end shape of the misdispatch above: a *complete*, internally
    coherent RandomForest design — not just the family string — must not become a
    `gradient_boosting_lags` design carrying `bootstrap`/`oob_score`."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(
            model_family="RandomForestRegressor",
            search_space={"n_estimators": {"type": "int", "low": 100, "high": 1000}},
            fixed_params={"bootstrap": True, "oob_score": False},
            rationale="Bagged trees over lag features decorrelate errors better here.",
        )
    )
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match="not a supported model family"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("SARIMAX", "arima"),
        ("auto-ARIMA", "arima"),
        ("FBProphet", "prophet"),
        ("Holt-Winters", "exponential_smoothing"),
        ("ETS", "exponential_smoothing"),
        ("lag features for XGBoost", "gradient_boosting_lags"),
        ("XGBoost", "gradient_boosting_lags"),
        ("LightGBM over lagged targets", "gradient_boosting_lags"),
        ("Ridge regression on lag features", "linear_lags"),
    ],
)
def test_model_family_alias_is_normalized_before_writing(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    raw: str,
    expected: str,
) -> None:
    """Realistic LLM phrasings, including the two spellings `\\barima\\b` cannot
    reach on its own (`SARIMAX`, `autoarima`) and the lag-feature modifier, which
    is discriminated by the model brand alone."""
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=raw))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == expected


@pytest.mark.parametrize(
    "raw", ["time series forecasting", "forecasting", "time series", "lag features"]
)
def test_generic_forecasting_phrasings_do_not_silently_map_to_a_family(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, raw: str
) -> None:
    """`specialist_selector`'s routing vocabulary is deliberately NOT aliased, and
    neither is a bare "lag features": mapping either to a family would be an
    arbitrary modeling decision made on the LLM's behalf. A hard rejection is the
    intended outcome — the prompt is what prevents it."""
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=raw))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match="not a supported model family"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


@pytest.mark.parametrize(
    "raw", ["ARIMA residuals boosted with XGBoost", "prophet or arima", "ETS plus a ridge on lags"]
)
def test_hybrid_two_family_response_rejected_as_ambiguous(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, raw: str
) -> None:
    """A hybrid naming two families is rejected rather than resolved by
    precedence: `coder` dispatches on the single written family, so silently
    dropping half the answer would build the wrong model."""
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=raw))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match="ambiguous"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


def test_unsupported_model_family_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Pins a known asymmetry: `specialist_selector` routes an LSTM forecasting
    plan *here* (timeseries precedes deep learning in its keyword order), but
    `lstm` is not one of this node's families. The prompt, not the table, is what
    keeps the LLM from answering with it."""
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family="lstm"))
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match="not a supported model family"):
        node(_build_state())


# -- prompt <-> validator agreement --


def test_prompt_contains_exactly_one_parseable_json_example() -> None:
    """Only the *first* ```json block is pinned by the tests below, so a second,
    contradicting worked example would reach the model unchecked.

    The § Search-space grammar block is intentionally not parseable — it shows
    `<int>`/`<number>` placeholders — so "exactly one block that parses" is the
    invariant, not "exactly one block".
    """
    prompt = PromptLoader().load("timeseries_specialist", "v1")
    blocks = re.findall(r"```json\n(.*?)\n```", prompt, flags=re.DOTALL)

    parseable = []
    for block in blocks:
        try:
            parseable.append(json.loads(block))
        except ValueError:
            continue

    assert len(parseable) == 1, (
        f"expected exactly one parseable ```json example, found {len(parseable)} "
        f"(of {len(blocks)} json blocks) — a second example is not validated by any test"
    )


def test_prompt_json_example_matches_this_modules_payload() -> None:
    """Keeps `VALID_DESIGN` — reused by every mocked response here — in sync with
    what the prompt actually asks for."""
    prompt = PromptLoader().load("timeseries_specialist", "v1")

    assert _first_json_block(prompt) == VALID_DESIGN


def test_prompt_json_example_passes_the_real_validator(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """A prompt documenting a payload the shared validator rejects would fail the
    run in production; catch it here instead."""
    prompt = PromptLoader().load("timeseries_specialist", "v1")
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(_first_json_block(prompt)))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert written["model_family"] == "gradient_boosting_lags"
    assert "n_lags" in written["search_space"]


def test_prompt_names_every_canonical_family_and_no_other() -> None:
    """The prompt pins five bare literal tokens and the table accepts five
    canonical keys; neither may drift away from the other. Adding a family to the
    table without offering it in the prompt makes it unreachable; offering one the
    table does not know aborts the phase."""
    prompt = PromptLoader().load("timeseries_specialist", "v1")
    marker = "exactly one of these five literal tokens:"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("Name one and only one.", start)
    listed = set(re.findall(r"`([^`\n]+)`", prompt[start:end]))

    assert listed == set(_MODEL_FAMILIES)


def test_every_preprocessing_token_the_prompt_recommends_satisfies_the_validator() -> None:
    """No token the prompt recommends may be one the shared validator rejects.

    The prompt tells the LLM to encode fit scope in the token itself
    (`rolling_mean_past_only`), which pushes tokens toward the shared 64-character
    limit. If a recommended token failed `_PREPROCESSING_STEP_RE`, the prompt's own
    advice would produce designs the validator rejects — a self-inflicted outage,
    on a node with no retry wrapper.
    """
    prompt = PromptLoader().load("timeseries_specialist", "v1")
    start = prompt.index("## Preprocessing scope")
    section = prompt[start : prompt.index("\n## ", start + 1)]
    backticked = set(re.findall(r"`([^`\n]+)`", section))

    candidates = backticked - _PROMPT_NON_TOKEN_MENTIONS
    assert candidates, "prompt no longer recommends any preprocessing token"
    for token in sorted(candidates):
        assert _PREPROCESSING_STEP_RE.fullmatch(token), (
            f"§ Preprocessing scope presents {token!r} ({len(token)} chars) as a step token, "
            f"but the shared validator rejects it (must match "
            f"{_PREPROCESSING_STEP_RE.pattern}). If it is not a recommendation, add it to "
            "_PROMPT_NON_TOKEN_MENTIONS."
        )


# -- tuple-shaped hyperparameters (ARIMA order / seasonal order) --


def test_tuple_shaped_order_as_json_array_in_choices_is_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Why the prompt mandates hyphenated string tokens *inside `search_space`*: an
    ARIMA `(p, d, q)` order is not expressible as a `choices` entry, because
    `choices` takes only JSON scalars.

    Scope note: this is the `choices` path only. `_validate_fixed_params` explicitly
    permits a flat list of scalars, so `fixed_params: {"order": [1, 1, 1]}` is
    *accepted* by the validator — the array ban there is a pipeline convention the
    prompt states so `coder` (T-029) sees exactly one encoding, not a rejection the
    schema enforces. See the T-027 entry in `context/discoveries.md`.
    """
    mock_llm.invoke.return_value = AIMessage(
        content=_design(
            search_space={"order": {"type": "categorical", "choices": [[1, 1, 1], [2, 1, 2]]}}
        )
    )
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match="JSON scalars"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


def test_string_token_order_choices_are_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """The first of the two encodings the prompt offers: `p-d-q` string tokens as
    categorical choices, written through unchanged so `coder` can parse a public
    format rather than a private one."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(
            model_family="arima",
            search_space={"order": {"type": "categorical", "choices": ["1-0-0", "1-1-1", "2-1-2"]}},
        )
    )
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert written["search_space"]["order"]["choices"] == ["1-0-0", "1-1-1", "2-1-2"]


def test_pinned_string_order_in_fixed_params_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """The second encoding: one pinned order, as a string in `fixed_params`."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(
            model_family="arima",
            fixed_params={"order": "1-1-1", "seasonal_order": "1-1-1-12"},
        )
    )
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert written["fixed_params"]["order"] == "1-1-1"
    assert written["fixed_params"]["seasonal_order"] == "1-1-1-12"


# -- prompt assembly --


def test_build_messages_includes_plan_and_fold_summary(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = [SOLUTION_PLAN, FOLD_CONFIG]
    node = TimeseriesSpecialistNode()

    node(_build_state())

    content = mock_llm.invoke.call_args[0][0][-1].content
    assert "## Solution plan" in content
    assert "## Frozen CV folds" in content
    assert "## Feature spec reference" in content

    # Assert each value sits under its OWN heading, not merely that both strings
    # are present somewhere: swapping the two labels would otherwise feed the LLM
    # the fold summary as the solution plan.
    plan_section = content.split("## Solution plan")[1].split("## Frozen CV folds")[0]
    folds_section = content.split("## Frozen CV folds")[1].split("## Feature spec reference")[0]
    assert "prophet" in plan_section
    assert "n_folds" in folds_section

    # The per-row fold assignments are never injected — they would flood the
    # context window with data the specialist has no use for.
    assert "fold_indices" not in content
    assert "222222" not in content


def test_build_messages_omits_fold_indices_but_keeps_non_time_aware_strategy(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """A frozen strategy that is not time-aware is still injected verbatim and the
    node still designs against it. The folds are write-once (CLAUDE.md invariant
    #1), so `stratified_kfold` on a forecasting problem is a fact of the run for
    this node to note in `rationale`, never one for it to correct."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = [SOLUTION_PLAN, FOLD_CONFIG]
    node = TimeseriesSpecialistNode()

    node(_build_state())

    content = mock_llm.invoke.call_args[0][0][-1].content
    assert "stratified_kfold" in content
    assert "fold_indices" not in content
    workspace_instance.write_json.assert_called_once()


def test_build_messages_handles_missing_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Phase 5 can be exercised standalone with no Phase 1/4 run ahead of it, so
    unset upstream paths degrade to a placeholder rather than raising."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["solution_plan_path"] = ""
    state["validation_config_path"] = ""
    node = TimeseriesSpecialistNode()

    node(state)

    workspace_instance.read_json.assert_not_called()
    assert "not yet available" in mock_llm.invoke.call_args[0][0][-1].content


def test_build_messages_handles_unreadable_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    node = TimeseriesSpecialistNode()

    node(_build_state())

    assert "unable to read" in mock_llm.invoke.call_args[0][0][-1].content


def test_build_messages_handles_corrupt_upstream_json(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """A truncated `solution_plan.json` raises `json.JSONDecodeError` out of
    `read_json`. Both readers in `_build_messages` must absorb it — hardening only
    the fold reader would leave the run just as dead."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = json.JSONDecodeError("truncated", "{", 0)
    node = TimeseriesSpecialistNode()

    node(_build_state())

    content = mock_llm.invoke.call_args[0][0][-1].content
    assert "unable to read solution plan" in content
    assert "unable to read frozen fold config" in content


def test_build_messages_handles_foreign_absolute_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Resuming a checkpointed run after the workspace moved leaves absolute
    paths that no longer sit under the current root — `relative_to_workspace`
    raises `ValueError`, which every reader in this node must absorb."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["solution_plan_path"] = "/old/workspace/design/iteration_0/solution_plan.json"
    state["validation_config_path"] = "/old/workspace/validation/fold_config.json"
    node = TimeseriesSpecialistNode()

    node(state)

    content = mock_llm.invoke.call_args[0][0][-1].content
    assert "unable to read solution plan" in content
    assert "unable to read frozen fold config" in content
    workspace_instance.write_json.assert_called_once()


def test_node_never_self_gates_on_temporal_signal(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """`specialist_selector` (T-023) is the sole gate. Given a wholly non-temporal
    plan and no frozen folds at all, this node must still call the LLM and still
    write a design — a refusal branch here would leave the iteration with zero
    artifacts and no other specialist queued behind it."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.return_value = {
        "model_families": ["logistic_regression"],
        "rationale": "no temporal structure",
    }
    state = _build_state()
    state["validation_config_path"] = ""
    node = TimeseriesSpecialistNode()

    node(state)

    mock_llm.invoke.assert_called_once()
    workspace_instance.write_json.assert_called_once()
    assert _written_design(workspace_instance)["specialist"] == "timeseries_specialist"


# -- response parsing / validation failures --


def test_json_in_json_fence_is_parsed(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=f"```json\n{RESPONSE_CONTENT}\n```")
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == "gradient_boosting_lags"


def test_invalid_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match="timeseries_specialist"):
        node(_build_state())


def test_nothing_written_when_validation_fails(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(search_space={}))
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match="search_space"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


def test_write_output_before_build_messages_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`_write_output` depends on the feature-spec reference `_build_messages`
    resolves; called out of `__call__`'s order it must refuse rather than write a
    design pointing at the wrong feature spec."""
    _, workspace_instance = mock_workspace_manager
    node = TimeseriesSpecialistNode()

    with pytest.raises(ValueError, match="timeseries_specialist"):
        node._write_output(
            workspace_instance,
            "experiments/exp_0/design.json",
            AIMessage(content=RESPONSE_CONTENT),
        )

    workspace_instance.write_json.assert_not_called()
