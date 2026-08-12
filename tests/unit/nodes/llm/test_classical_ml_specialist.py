"""Unit tests for src/nodes/llm/classical_ml_specialist.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at both import locations, matching
`test_feature_engineer.py`'s convention). No network calls, no real
filesystem writes.

The `design.json` schema rules themselves are covered exhaustively in
`test_experiment_design.py`; the cases here are the node's own contract —
output path, injected fields, prompt assembly, and the CV-redefinition
rejection reaching the caller.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings
from src.nodes.llm._experiment_design import FORBIDDEN_CV_KEYS
from src.nodes.llm.classical_ml_specialist import ClassicalMlSpecialistNode
from src.state import new_state

VALID_DESIGN: dict[str, Any] = {
    "model_family": "lightgbm",
    "search_space": {
        "n_estimators": {"type": "int", "low": 200, "high": 2000, "step": 100},
        "learning_rate": {"type": "float", "low": 0.005, "high": 0.3, "log": True},
    },
    "fixed_params": {"objective": "binary"},
    "preprocessing": ["native_categorical_handling"],
    "rationale": "Mixed-type tabular data with moderate cardinality.",
}
RESPONSE_CONTENT = json.dumps(VALID_DESIGN)
SOLUTION_PLAN = {"model_families": ["gradient_boosting"], "order": ["gradient_boosting"]}
FOLD_CONFIG = {
    "strategy": "stratified_kfold",
    "n_folds": 5,
    "seed": 42,
    "fold_indices": [{"train": [111111], "val": [222222]}],
}


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
    `src.nodes.llm.classical_ml_specialist` (the node's own instance, built in
    `_build_messages` to read the upstream artifacts) — resolving to the same
    mock instance, since neither call site knows about the other's."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.return_value = SOLUTION_PLAN
    instance.write_json.return_value = "/workspace/experiments/exp_0/design.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.classical_ml_specialist.WorkspaceManager") as mock_wm_cls_node,
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
    config = load_agent_config("classical_ml_specialist")

    assert config.name == "classical_ml_specialist"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "experiments/exp_{iteration}/design.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("classical_ml_specialist", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — classical_ml_specialist" in prompt


# -- __call__ behavior --


def test_call_writes_design_with_search_space_and_model_family(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ClassicalMlSpecialistNode()

    node(_build_state())

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "experiments/exp_0/design.json"
    assert "search_space" in args[1]
    assert "model_family" in args[1]
    assert args[1]["model_family"] == "lightgbm"


def test_output_path_uses_current_iteration(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ClassicalMlSpecialistNode()

    node(_build_state(current_iteration=3))

    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "experiments/exp_3/design.json"


def test_delta_adds_no_new_labstate_field(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`coder` (T-029) reads `experiments/exp_{iteration}/design.json` from its
    well-known path, so this node must not introduce a `LabState` field —
    `src/state.py` is a protected contract."""
    node = ClassicalMlSpecialistNode()

    delta = node(_build_state())

    assert set(delta) == {"messages"}


# -- frozen folds (Done when: "the design references the frozen folds") --


def test_written_design_references_frozen_folds(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(cv_strategy_ref="my_own_folds.json"))
    _, workspace_instance = mock_workspace_manager
    node = ClassicalMlSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["cv_strategy_ref"] == "validation/fold_config.json"


def test_written_design_references_frozen_folds_when_llm_omits_the_key(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ClassicalMlSpecialistNode()

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
    node = ClassicalMlSpecialistNode()

    with pytest.raises(ValueError, match=key):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()


# -- injected fields --


def test_specialist_field_injected_not_taken_from_llm(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(specialist="nlp_specialist"))
    _, workspace_instance = mock_workspace_manager
    node = ClassicalMlSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["specialist"] == "classical_ml_specialist"


def test_feature_spec_ref_relativized_from_state(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """An absolute host path baked into `design.json` breaks inside
    `code_executor`'s subprocess and inside the container, where the workspace is
    bind-mounted elsewhere."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["feature_spec_path"] = "/workspace/design/iteration_0/feature_spec.json"
    node = ClassicalMlSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["feature_spec_ref"] == "design/iteration_0/feature_spec.json"


def test_feature_spec_ref_falls_back_when_unset(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    state = _build_state(current_iteration=2)
    state["feature_spec_path"] = ""
    node = ClassicalMlSpecialistNode()

    node(state)

    written = _written_design(workspace_instance)
    assert written["feature_spec_ref"] == "design/iteration_2/feature_spec.json"


def test_n_trials_from_llm_not_written(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """The trial budget lives in `config/settings.yaml`'s `optuna:` block, never
    in a per-experiment design."""
    mock_llm.invoke.return_value = AIMessage(
        content=_design(n_trials=500, early_stopping_patience=99)
    )
    _, workspace_instance = mock_workspace_manager
    node = ClassicalMlSpecialistNode()

    node(_build_state())

    written = _written_design(workspace_instance)
    assert "n_trials" not in written
    assert "early_stopping_patience" not in written


# -- prompt assembly --


def test_build_messages_includes_plan_and_fold_summary(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = [SOLUTION_PLAN, FOLD_CONFIG]
    node = ClassicalMlSpecialistNode()

    node(_build_state())

    last_message = mock_llm.invoke.call_args[0][0][-1]
    assert "## Solution plan" in last_message.content
    assert "gradient_boosting" in last_message.content
    assert "## Frozen CV folds" in last_message.content
    assert "stratified_kfold" in last_message.content
    assert "## Feature spec reference" in last_message.content
    # The per-row fold assignments are never injected — they would flood the
    # context window with data the specialist has no use for.
    assert "fold_indices" not in last_message.content
    assert "222222" not in last_message.content


def test_build_messages_handles_missing_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Phase 5 can be exercised standalone with no Phase 1/4 run ahead of it, so
    unset upstream paths degrade to a placeholder rather than raising."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["solution_plan_path"] = ""
    state["validation_config_path"] = ""
    node = ClassicalMlSpecialistNode()

    node(state)

    workspace_instance.read_json.assert_not_called()
    assert "not yet available" in mock_llm.invoke.call_args[0][0][-1].content


def test_build_messages_handles_unreadable_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    node = ClassicalMlSpecialistNode()

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
    node = ClassicalMlSpecialistNode()

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
    node = ClassicalMlSpecialistNode()

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
    node = ClassicalMlSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == "lightgbm"


def test_invalid_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    node = ClassicalMlSpecialistNode()

    with pytest.raises(ValueError, match="classical_ml_specialist"):
        node(_build_state())


def test_model_family_alias_is_normalized_before_writing(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """`coder` (T-029) dispatches on the written `model_family`, so an alias the
    LLM used must be canonicalized against the node's own `_MODEL_FAMILIES`
    table, not passed through."""
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family="XGB"))
    _, workspace_instance = mock_workspace_manager
    node = ClassicalMlSpecialistNode()

    node(_build_state())

    assert _written_design(workspace_instance)["model_family"] == "xgboost"


def test_unsupported_model_family_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(model_family="random_forest"))
    node = ClassicalMlSpecialistNode()

    with pytest.raises(ValueError, match="not a supported model family"):
        node(_build_state())


def test_nothing_written_when_validation_fails(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_design(search_space={}))
    _, workspace_instance = mock_workspace_manager
    node = ClassicalMlSpecialistNode()

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
    node = ClassicalMlSpecialistNode()

    with pytest.raises(ValueError, match="classical_ml_specialist"):
        node._write_output(
            workspace_instance,
            "experiments/exp_0/design.json",
            AIMessage(content=RESPONSE_CONTENT),
        )

    workspace_instance.write_json.assert_not_called()
