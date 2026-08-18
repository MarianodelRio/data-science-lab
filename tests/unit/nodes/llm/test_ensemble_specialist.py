"""Unit tests for src/nodes/llm/ensemble_specialist.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at both import locations, matching
`test_timeseries_specialist.py`'s convention). No network calls, no real
filesystem writes.

The `design.json` schema rules shared with the other Phase 5 specialists are
covered exhaustively in `test_experiment_design.py`; the cases here are the
node's own contract — output path, node-injected `base_experiments` (the OOF
discovery convention, the `experiment_id`/directory fallbacks, the >=1 vs >=2
boundary), its own three-family model-family table, and prompt assembly.
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
from src.nodes.llm.ensemble_specialist import (
    _MODEL_FAMILIES,
    EnsembleSpecialistNode,
)
from src.state import new_state

CANONICAL_FAMILIES = {"stacking", "blending", "weighted_average"}

# The worked example from `config/prompts/ensemble_specialist/v1.md`.
# `test_prompt_json_example_matches_this_modules_payload` pins the two together,
# so the mocked responses here stay the shape the prompt actually asks for.
VALID_DESIGN: dict[str, Any] = {
    "model_family": "stacking",
    "search_space": {
        "alpha": {"type": "float", "low": 0.001, "high": 10.0, "log": True},
        "fit_intercept": {"type": "categorical", "choices": [True, False]},
    },
    "fixed_params": {},
    "preprocessing": ["meta_features_from_oof_predictions"],
    "rationale": (
        "Ridge over the base experiments' out-of-fold predictions is a cheap, well-behaved "
        "meta-learner: alpha is tuned across a wide log range since the right amount of "
        "shrinkage depends on how correlated the base predictions are, and fit_intercept is "
        "left open in case the base models are systematically biased."
    ),
}
RESPONSE_CONTENT = json.dumps(VALID_DESIGN)
SOLUTION_PLAN = {
    "model_families": ["lightgbm", "xgboost"],
    "ensembling_strategy": "stack the top two models",
}
SOLUTION_PLAN_PATH = "design/iteration_0/solution_plan.json"
FOLD_CONFIG_PATH = "validation/fold_config.json"
# `stratified_kfold` on purpose: the frozen strategy is a read-only fact of the run.
FOLD_CONFIG = {
    "strategy": "stratified_kfold",
    "n_folds": 5,
    "seed": 42,
    "fold_indices": [{"train": [111111], "val": [222222]}],
}

EXPERIMENTS = [
    {"id": "exp-0", "path": "experiments/exp_0", "cv_score": 0.81, "iteration": 0, "model": "lgbm"},
    {"id": "exp-1", "path": "experiments/exp_1", "cv_score": 0.83, "iteration": 1, "model": "xgb"},
]
# exp-0's results.json carries an explicit `oof_path`; exp-1's does not, so it falls
# back to the `oof_predictions.parquet` convention in its own experiment directory.
RESULTS_EXP_0 = {"oof_path": "experiments/exp_0/oof_predictions.parquet", "cv_score": 0.81}
RESULTS_EXP_1 = {"cv_score": 0.83}
EXPECTED_BASE_EXPERIMENTS = [
    {"experiment_id": "exp-0", "oof_path": "experiments/exp_0/oof_predictions.parquet"},
    {"experiment_id": "exp-1", "oof_path": "experiments/exp_1/oof_predictions.parquet"},
]

# (alias the LLM might write, canonical family it must resolve to) for every alias
# in the real table.
_ALIAS_CASES = [(alias, family) for family, aliases in _MODEL_FAMILIES.items() for alias in aliases]

# Backticked strings in the prompt's § Preprocessing scope section that are
# deliberately NOT step-name recommendations: the shape regex itself and the two
# call forms the section shows as *rejected*. Inverted (allowlist) rather than
# regex-extracted, so a new bad recommendation fails by default — same guard as
# `test_timeseries_specialist.py`.
_PROMPT_NON_TOKEN_MENTIONS = frozenset(
    {
        "^[a-z][a-z0-9_]{0,63}$",
        "RidgeCV(alphas=[0.1, 1.0])",
        "np.average(preds, weights=w)",
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
    """Parse the first ```json fenced block of the prompt — the worked example."""
    marker = "```json\n"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("\n```", start)
    parsed = json.loads(prompt[start:end])
    assert isinstance(parsed, dict)
    return parsed


def _read_json_router(overrides: dict[str, Any] | None = None):
    """Builds a `WorkspaceManager.read_json` `side_effect` that routes by the
    relative path argument: `overrides` (an exception instance raises, anything
    else is returned) take priority, then the well-known solution-plan / fold-
    config paths, then an explicit failure for any unexpected path — so a test
    forgetting to stub a path fails loudly instead of silently misreading
    another fixture's data."""
    routes = dict(overrides or {})

    def _router(relative_path: str) -> Any:
        if relative_path in routes:
            value = routes[relative_path]
            if isinstance(value, Exception):
                raise value
            return value
        if relative_path == SOLUTION_PLAN_PATH:
            return SOLUTION_PLAN
        if relative_path == FOLD_CONFIG_PATH:
            return FOLD_CONFIG
        raise OSError(f"unexpected read_json call in test: {relative_path}")

    return _router


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
    `src.nodes.llm.ensemble_specialist` (the node's own instance, built in
    `_build_messages` to read the upstream artifacts) — resolving to the same mock
    instance, since neither call site knows about the other's."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.side_effect = _read_json_router(
        {
            "experiments/exp_0/results.json": RESULTS_EXP_0,
            "experiments/exp_1/results.json": RESULTS_EXP_1,
        }
    )
    instance.write_json.return_value = "/workspace/experiments/exp_0/design.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.ensemble_specialist.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0, experiments: list[dict] | None = None) -> Any:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    state["solution_plan_path"] = SOLUTION_PLAN_PATH
    state["validation_config_path"] = FOLD_CONFIG_PATH
    state["feature_spec_path"] = "design/iteration_0/feature_spec.json"
    state["experiments"] = EXPERIMENTS if experiments is None else experiments
    return state


def _written_design(workspace_instance: MagicMock) -> dict[str, Any]:
    args, _ = workspace_instance.write_json.call_args
    return args[1]


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("ensemble_specialist")

    assert config.name == "ensemble_specialist"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "experiments/exp_{iteration}/design.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("ensemble_specialist", "v1")
    assert prompt.strip() != ""
    assert prompt.splitlines()[0] == "# System prompt — ensemble_specialist"


# -- __call__ behavior --


def test_call_writes_design_with_search_space_and_stacking_family(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "experiments/exp_0/design.json"
    assert args[1]["model_family"] == "stacking"
    assert "alpha" in args[1]["search_space"]


def test_output_path_uses_current_iteration(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state(current_iteration=3))

    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "experiments/exp_3/design.json"


def test_delta_adds_no_new_labstate_field(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`coder` (T-029) reads `experiments/exp_{iteration}/design.json` from its
    well-known path, so this node must not introduce a `LabState` field —
    `src/state.py` is a protected contract."""
    node = EnsembleSpecialistNode()

    delta = node(_build_state())

    assert set(delta) == {"messages"}


# -- base_experiments (Done when: "listing the source OOF paths", "uses OOF predictions") --


def test_written_design_lists_all_base_experiments_oof_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["base_experiments"] == EXPECTED_BASE_EXPERIMENTS


def test_oof_path_sourced_from_results_json_when_present(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """exp-0's `results.json` carries an explicit `oof_path` — used verbatim
    (re-relativized), never the `oof_predictions.parquet` fallback."""
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert written["base_experiments"][0] == {
        "experiment_id": "exp-0",
        "oof_path": "experiments/exp_0/oof_predictions.parquet",
    }


def test_oof_path_falls_back_to_parquet_convention_when_results_json_lacks_field(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """exp-1's `results.json` has no `oof_path` key — falls back to the
    well-known `oof_predictions.parquet` file in its own directory."""
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert written["base_experiments"][1] == {
        "experiment_id": "exp-1",
        "oof_path": "experiments/exp_1/oof_predictions.parquet",
    }


def test_oof_path_falls_back_to_parquet_convention_when_results_json_unreadable(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_0/results.json": OSError("no results.json for this experiment")}
    )
    state = _build_state(experiments=[EXPERIMENTS[0]])
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "exp-0", "oof_path": "experiments/exp_0/oof_predictions.parquet"}
    ]


def test_experiment_id_falls_back_to_positional_when_entry_lacks_id(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_0/results.json": {"oof_path": "experiments/exp_0/preds.parquet"}}
    )
    state = _build_state(experiments=[{"path": "experiments/exp_0", "iteration": 0}])
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "experiment_0", "oof_path": "experiments/exp_0/preds.parquet"}
    ]


def test_experiment_dir_treats_suffixed_path_as_file_pointer_and_uses_parent(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """A `path` with a file suffix (e.g. `coder`'s own `train.py` pointer
    convention) is treated as a file inside the experiment directory, not the
    directory itself — its parent is used, mirroring
    `code_critic._experiment_dir_from_state`'s normalization."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_0/results.json": {"oof_path": "experiments/exp_0/preds.parquet"}}
    )
    state = _build_state(
        experiments=[{"id": "exp-0", "path": "experiments/exp_0/train.py", "iteration": 0}]
    )
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "exp-0", "oof_path": "experiments/exp_0/preds.parquet"}
    ]


def test_experiment_dir_falls_back_to_list_position_when_entry_has_no_iteration_field(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """When both `path` and `iteration` are absent, the fallback directory uses
    the entry's position in `state["experiments"]`, not a raw `None`."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_0/results.json": {"oof_path": "experiments/exp_0/preds.parquet"}}
    )
    state = _build_state(experiments=[{"id": "exp-only-id"}])
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "exp-only-id", "oof_path": "experiments/exp_0/preds.parquet"}
    ]


def test_oof_path_falls_back_to_parquet_convention_when_results_json_is_not_an_object(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`results.json` parsing to a JSON array/scalar rather than an object
    degrades to the parquet fallback, same as an unreadable file."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_0/results.json": ["not", "an", "object"]}
    )
    state = _build_state(experiments=[EXPERIMENTS[0]])
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "exp-0", "oof_path": "experiments/exp_0/oof_predictions.parquet"}
    ]


def test_experiment_dir_falls_back_when_path_missing(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_3/results.json": {"oof_path": "experiments/exp_3/preds.parquet"}}
    )
    state = _build_state(experiments=[{"id": "exp-3", "iteration": 3}])
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "exp-3", "oof_path": "experiments/exp_3/preds.parquet"}
    ]


def test_experiment_dir_falls_back_when_path_is_traversal(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_0/results.json": {"oof_path": "experiments/exp_0/preds.parquet"}}
    )
    state = _build_state(experiments=[{"id": "exp-0", "path": "../outside/exp_0", "iteration": 0}])
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "exp-0", "oof_path": "experiments/exp_0/preds.parquet"}
    ]


def test_experiment_dir_falls_back_when_path_is_absolute_outside_workspace(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_2/results.json": {"oof_path": "experiments/exp_2/preds.parquet"}}
    )
    state = _build_state(experiments=[{"id": "exp-2", "path": "/elsewhere/exp_0", "iteration": 2}])
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "exp-2", "oof_path": "experiments/exp_2/preds.parquet"}
    ]


def test_zero_experiments_in_state_raises_and_writes_nothing(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    state = _build_state(experiments=[])
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match="base_experiments"):
        node(state)

    workspace_instance.write_json.assert_not_called()


def test_exactly_one_experiment_still_writes(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """Documents the `>= 1` (schema) vs `>= 2` (`specialist_selector`'s routing
    eligibility) boundary: the shared validator only requires a non-empty
    `base_experiments`, mirroring `search_space`'s "must not be empty" floor —
    the `>= 2` rule belongs to `specialist_selector._should_ensemble`, not this
    schema, and is not re-derived here."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_0/results.json": RESULTS_EXP_0}
    )
    state = _build_state(experiments=[EXPERIMENTS[0]])
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "exp-0", "oof_path": "experiments/exp_0/oof_predictions.parquet"}
    ]


def test_llm_sent_base_experiments_are_discarded(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """`base_experiments` is node-injected — whatever the LLM sends under that
    key in its own response must never reach the written design."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(base_experiments=[{"experiment_id": "llm-made-up", "oof_path": "nowhere"}])
    )
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["base_experiments"] == EXPECTED_BASE_EXPERIMENTS


# -- fallback-numbering collision (adversarial review BLOCKER, T-028 fix) --


def test_two_degraded_entries_with_colliding_fallback_numbers_raise_and_write_nothing(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """The exact BLOCKER reproducer: two `state["experiments"]` entries with no
    usable `path` — `{"iteration": 1}` and `{}` — whose fallback numbers both
    resolve to `1` (the first via its own recorded `iteration`, the second via
    its list position). Before this fix, `_experiment_id` numbered by list
    index while `_fallback_iteration` numbered by the entry's own `iteration`,
    so the two got distinct ids (`experiment_0`/`experiment_1`) sharing one
    `oof_path` (`experiments/exp_1/oof_predictions.parquet`) — written silently.
    Now both id and directory are derived from the same `_fallback_iteration`
    value, so the two entries collide identically and
    `_validate_base_experiments` rejects the design outright."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_1/results.json": OSError("no results.json for this experiment")}
    )
    state = _build_state(experiments=[{"iteration": 1}, {}])
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match="both resolve to the same 'oof_path'"):
        node(state)

    workspace_instance.write_json.assert_not_called()


def test_two_legitimately_distinct_degraded_entries_write_successfully(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """Two entries that are both missing `path` but carry distinct recorded
    `iteration` values degrade to distinct fallback numbers, so their ids and
    `oof_path`s stay distinct and the design still writes — pins that the
    duplicate-`oof_path` rejection catches only genuine collisions, not every
    degraded entry."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {
            "experiments/exp_1/results.json": OSError("no results.json"),
            "experiments/exp_2/results.json": OSError("no results.json"),
        }
    )
    state = _build_state(experiments=[{"iteration": 1}, {"iteration": 2}])
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "experiment_1", "oof_path": "experiments/exp_1/oof_predictions.parquet"},
        {"experiment_id": "experiment_2", "oof_path": "experiments/exp_2/oof_predictions.parquet"},
    ]


def test_degraded_entry_fallback_id_and_directory_agree(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """The unified-numbering pin: a degraded entry's fallback id
    (`experiment_{n}`) and the directory used to look up its `results.json`
    are always the same `n` — here `5`, from the entry's own recorded
    `iteration` since `path` is absent. If the two ever numbered
    independently again, this entry's id would say `5` while the lookup would
    hit a different (unstubbed) directory and raise, instead of resolving the
    real `oof_path` below."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = _read_json_router(
        {"experiments/exp_5/results.json": {"oof_path": "experiments/exp_5/preds.parquet"}}
    )
    state = _build_state(experiments=[{"iteration": 5}])
    node = EnsembleSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["base_experiments"] == [
        {"experiment_id": "experiment_5", "oof_path": "experiments/exp_5/preds.parquet"}
    ]


# -- frozen folds (Done when: "the design references the frozen folds") --


def test_written_design_references_frozen_folds(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(cv_strategy_ref="my_own_folds.json"))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["cv_strategy_ref"] == "validation/fold_config.json"


def test_written_design_references_frozen_folds_when_llm_omits_the_key(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["cv_strategy_ref"] == "validation/fold_config.json"


@pytest.mark.parametrize("key", sorted(FORBIDDEN_CV_KEYS))
def test_llm_cv_key_at_top_level_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, key: str
) -> None:
    """A design that tries to redefine cross-validation is rejected outright and
    nothing is written — the folds are frozen (CLAUDE.md invariant #1)."""
    mock_llm.invoke.return_value = AIMessage(content=_design(**{key: "whatever"}))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match=key):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


@pytest.mark.parametrize("key", sorted(FORBIDDEN_CV_KEYS))
def test_llm_cv_key_in_search_space_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, key: str
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(search_space={key: "whatever"}))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match=key):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


@pytest.mark.parametrize("key", sorted(FORBIDDEN_CV_KEYS))
def test_llm_cv_key_in_fixed_params_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, key: str
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(fixed_params={key: "whatever"}))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match=key):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


# -- injected fields --


def test_specialist_field_injected_not_taken_from_llm(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(specialist="nlp_specialist"))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["specialist"] == "ensemble_specialist"


def test_feature_spec_ref_relativized_from_state(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["feature_spec_path"] = "/workspace/design/iteration_0/feature_spec.json"
    node = EnsembleSpecialistNode()

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
    node = EnsembleSpecialistNode()

    node(state)

    assert (
        _written_design(workspace_instance)["feature_spec_ref"]
        == "design/iteration_2/feature_spec.json"
    )


def test_n_trials_from_llm_not_written(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_design(n_trials=500, early_stopping_patience=99)
    )
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert "n_trials" not in written
    assert "early_stopping_patience" not in written


# -- search_space shape --


def test_nothing_written_when_search_space_empty(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(search_space={}))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match="search_space"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


def test_weighted_average_design_with_index_named_weights_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_design(
            model_family="weighted_average",
            search_space={
                "weight_0": {"type": "float", "low": 0.0, "high": 1.0},
                "weight_1": {"type": "float", "low": 0.0, "high": 1.0},
            },
        )
    )
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert written["model_family"] == "weighted_average"
    assert set(written["search_space"]) == {"weight_0", "weight_1"}


# -- model-family table integrity --


def test_model_family_table_is_exactly_the_three_families() -> None:
    """Pins the human-checkpoint decision: three canonical families, no more."""
    assert set(_MODEL_FAMILIES) == CANONICAL_FAMILIES


@pytest.mark.parametrize("family", sorted(CANONICAL_FAMILIES))
def test_every_canonical_key_resolves_to_itself(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, family: str
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=family))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

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
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=alias))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == expected_family


def test_weighted_blend_of_stacked_models_resolves_to_blending(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """The defusing case the alias table is built around: `"stacked"` is not
    aliased anywhere, so only `blending`'s own "weighted blend" alias matches."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(model_family="weighted blend of stacked models")
    )
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == "blending"


@pytest.mark.parametrize(
    "raw",
    [
        "blended stacking",
        "stacking and blending combined",
        "a meta learner that blends the outputs",
        # Adversarial review Finding 3 — plausible LLM phrasings the prompt's
        # rejected-examples list did not previously warn against, each naming
        # two families at once via the qualified multi-word aliases.
        "super learner blend",
        "weighted average of blends",
        "convex combination blend",
        "holdout blend with learned weights",
        "a stacked super learner with weighted blend",
    ],
)
def test_ambiguous_multiword_phrasings_raise(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, raw: str
) -> None:
    """These CANNOT be defused by the alias table alone: each phrase carries
    qualified multi-word aliases from two different families at once (e.g.
    "blended"/`blending` plus "stacking"/`stacking`, or "weighted blend"/
    `blending` plus "convex combination"/`weighted_average`), so it is
    structurally ambiguous by design — see the prompt's § model_family section
    and the T-028 entry in context/decisions.md."""
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=raw))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match="ambiguous"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


@pytest.mark.parametrize("raw", ["random forest", "voting"])
def test_unsupported_model_family_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, raw: str
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family=raw))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match="not a supported model family"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


# -- prompt <-> validator agreement --


def test_prompt_contains_exactly_one_parseable_json_example() -> None:
    prompt = PromptLoader().load("ensemble_specialist", "v1")
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
    prompt = PromptLoader().load("ensemble_specialist", "v1")

    assert _first_json_block(prompt) == VALID_DESIGN


def test_prompt_json_example_passes_the_real_validator(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    prompt = PromptLoader().load("ensemble_specialist", "v1")
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(_first_json_block(prompt)))
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert written["model_family"] == "stacking"
    assert "alpha" in written["search_space"]
    assert written["base_experiments"] == EXPECTED_BASE_EXPERIMENTS


def test_prompt_names_every_canonical_family_and_no_other() -> None:
    prompt = PromptLoader().load("ensemble_specialist", "v1")
    marker = "exactly one of three literal tokens:"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("Name one and only one.", start)
    listed = set(re.findall(r"`([^`\n]+)`", prompt[start:end]))

    assert listed == set(_MODEL_FAMILIES)


def test_every_preprocessing_token_the_prompt_recommends_satisfies_the_validator() -> None:
    prompt = PromptLoader().load("ensemble_specialist", "v1")
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


# -- prompt assembly --


def test_build_messages_includes_all_four_sections(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    content = mock_llm.invoke.call_args[0][0][-1].content
    assert "## Solution plan" in content
    assert "## Frozen CV folds" in content
    assert "## Feature spec reference" in content
    assert "## Base experiments (out-of-fold predictions)" in content

    plan_section = content.split("## Solution plan")[1].split("## Frozen CV folds")[0]
    folds_section = content.split("## Frozen CV folds")[1].split("## Feature spec reference")[0]
    base_section = content.split("## Base experiments (out-of-fold predictions)")[1]
    assert "lightgbm" in plan_section
    assert "n_folds" in folds_section
    assert "exp-0" in base_section
    assert "exp-1" in base_section
    assert "oof_predictions.parquet" in base_section

    # The per-row fold assignments are never injected.
    assert "fold_indices" not in content
    assert "222222" not in content


def test_build_messages_renders_placeholder_when_no_base_experiments(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    state = _build_state(experiments="not-a-list")  # degrades to []
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match="base_experiments"):
        node(state)

    content = mock_llm.invoke.call_args[0][0][-1].content
    assert "(no base experiments recorded in state)" in content
    workspace_instance.write_json.assert_not_called()


def test_build_messages_handles_missing_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Phase 5 can be exercised standalone with no Phase 1/4 run ahead of it, so
    unset upstream paths degrade to a placeholder rather than raising —
    independent of `base_experiments`, which is built from
    `state["experiments"]` alone."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["solution_plan_path"] = ""
    state["validation_config_path"] = ""
    node = EnsembleSpecialistNode()

    node(state)

    content = mock_llm.invoke.call_args[0][0][-1].content
    assert "not yet available" in content
    workspace_instance.write_json.assert_called_once()


def test_build_messages_handles_unreadable_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    node = EnsembleSpecialistNode()

    node(_build_state())

    assert "unable to read" in mock_llm.invoke.call_args[0][0][-1].content


def test_build_messages_handles_corrupt_upstream_json(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """A truncated `solution_plan.json` raises `json.JSONDecodeError` out of
    `read_json`. Both readers in `_build_messages` must absorb it — hardening
    only one would leave the run just as dead."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = json.JSONDecodeError("truncated", "{", 0)
    node = EnsembleSpecialistNode()

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
    node = EnsembleSpecialistNode()

    node(state)

    content = mock_llm.invoke.call_args[0][0][-1].content
    assert "unable to read solution plan" in content
    assert "unable to read frozen fold config" in content
    workspace_instance.write_json.assert_called_once()


# -- response parsing / validation failures --


def test_json_in_json_fence_is_parsed(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=f"```json\n{RESPONSE_CONTENT}\n```")
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == "stacking"


def test_invalid_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match="ensemble_specialist"):
        node(_build_state())


def test_write_output_before_build_messages_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`_write_output` depends on the feature-spec reference and
    `base_experiments`, both resolved by `_build_messages`; called out of
    `__call__`'s order it must refuse rather than write a design pointing at
    unknown inputs."""
    _, workspace_instance = mock_workspace_manager
    node = EnsembleSpecialistNode()

    with pytest.raises(ValueError, match="ensemble_specialist"):
        node._write_output(
            workspace_instance,
            "experiments/exp_0/design.json",
            AIMessage(content=RESPONSE_CONTENT),
        )

    workspace_instance.write_json.assert_not_called()
