"""Unit tests for src/nodes/llm/baseline_designer.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at their import location inside
`src.nodes.llm.base`, matching `test_problem_framer.py`'s convention). No
network calls, no real filesystem writes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings
from src.nodes.llm.baseline_designer import BaselineDesignerNode
from src.state import new_state

VALID_DESIGN = {
    "model": "lightgbm",
    "hyperparameters": {"n_estimators": 100, "max_depth": -1},
    "features": "all",
    "target_column": "target",
}
EXPECTED_WRITTEN = {**VALID_DESIGN, "cv_strategy_ref": "validation/fold_config.json"}
RESPONSE_CONTENT = json.dumps(VALID_DESIGN)
PROBLEM_DEFINITION = {
    "problem_type": "binary_classification",
    "success_metric": "roc_auc",
    "constraints": [],
}
EDA_REPORT_TEXT = "# EDA Report\n\nOne file found: `data/raw/train.csv`."


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
    """Patched at both import locations: `src.nodes.llm.base` (used by the
    base class's own `__call__` to write output) and
    `src.nodes.llm.baseline_designer` (the node's own instance, constructed
    in its `_build_messages` override to read the upstream artifacts) —
    both must resolve to the same mock instance since neither call site is
    aware of the other's WorkspaceManager."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.return_value = PROBLEM_DEFINITION
    instance.read_text.return_value = EDA_REPORT_TEXT
    instance.write_json.return_value = "/workspace/experiments/baseline/design.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.baseline_designer.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    state["problem_definition_path"] = "reports/problem_definition.json"
    state["eda_report_path"] = "reports/eda_report.md"
    return state


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("baseline_designer")

    assert config.name == "baseline_designer"
    assert config.model_role == "implementation"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "experiments/baseline/design.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("baseline_designer", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — baseline_designer" in prompt


# -- __call__ behavior --


def test_call_writes_design_via_workspace_write_json(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = BaselineDesignerNode()
    state = _build_state()

    node(state)

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "experiments/baseline/design.json"
    assert args[1] == EXPECTED_WRITTEN


def test_call_reads_problem_definition_and_eda_report(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = BaselineDesignerNode()
    state = _build_state()

    node(state)

    workspace_instance.read_json.assert_called_once_with("reports/problem_definition.json")
    workspace_instance.read_text.assert_called_once_with("reports/eda_report.md")


def test_build_messages_includes_both_sections(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    node = BaselineDesignerNode()
    state = _build_state()

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "## Problem definition" in last_message.content
    assert "binary_classification" in last_message.content
    assert "## EDA report" in last_message.content
    assert EDA_REPORT_TEXT in last_message.content


def test_call_sets_no_new_state_field(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    node = BaselineDesignerNode()
    state = _build_state()

    delta = node(state)

    assert set(delta.keys()) == {"messages"}


def test_relative_to_workspace_converts_absolute_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = BaselineDesignerNode()
    state = _build_state()
    state["problem_definition_path"] = "/workspace/reports/problem_definition.json"
    state["eda_report_path"] = "/workspace/reports/eda_report.md"

    node(state)

    workspace_instance.read_json.assert_called_once_with("reports/problem_definition.json")
    workspace_instance.read_text.assert_called_once_with("reports/eda_report.md")


def test_json_in_json_fence_is_parsed(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=f"```json\n{RESPONSE_CONTENT}\n```")
    _, workspace_instance = mock_workspace_manager
    node = BaselineDesignerNode()
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1] == EXPECTED_WRITTEN


def test_cv_strategy_ref_is_always_injected_regardless_of_llm_output(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """cv_strategy_ref must never be trusted from the LLM — it is always
    overwritten with the fixed pointer, even if the LLM tried to set a
    different value."""
    data = {**VALID_DESIGN, "cv_strategy_ref": "some/other/path.json"}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    _, workspace_instance = mock_workspace_manager
    node = BaselineDesignerNode()
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1]["cv_strategy_ref"] == "validation/fold_config.json"


def test_invalid_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    node = BaselineDesignerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="baseline_designer"):
        node(state)


def test_non_dict_top_level_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps([VALID_DESIGN]))
    node = BaselineDesignerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="baseline_designer"):
        node(state)


@pytest.mark.parametrize("missing_field", ["model", "hyperparameters", "features", "target_column"])
def test_missing_required_key_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, missing_field: str
) -> None:
    data = dict(VALID_DESIGN)
    del data[missing_field]
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = BaselineDesignerNode()
    state = _build_state()

    with pytest.raises(ValueError, match=missing_field):
        node(state)


def test_empty_model_string_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_DESIGN, "model": "   "}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = BaselineDesignerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="model"):
        node(state)


def test_hyperparameters_wrong_type_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_DESIGN, "hyperparameters": "not a dict"}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = BaselineDesignerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="hyperparameters"):
        node(state)


def test_features_list_of_strings_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_DESIGN, "features": ["a", "b", "c"]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    _, workspace_instance = mock_workspace_manager
    node = BaselineDesignerNode()
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1]["features"] == ["a", "b", "c"]


def test_features_list_containing_target_column_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    # Defense-in-depth against target leakage: even though baseline_runner's
    # generated training script also excludes target_column from an explicit
    # features list unconditionally, a design that leaks the target should
    # never be written to disk as the LLM's stated intent in the first place.
    data = {**VALID_DESIGN, "features": ["a", "b", VALID_DESIGN["target_column"]]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = BaselineDesignerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="must not include the target column"):
        node(state)


@pytest.mark.parametrize(
    "bad_features",
    ["not-all-or-list", 123, ["a", 1, "c"], None],
)
def test_features_invalid_value_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, bad_features: Any
) -> None:
    data = {**VALID_DESIGN, "features": bad_features}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = BaselineDesignerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="features"):
        node(state)


# -- missing/unreadable upstream artifacts --


def test_build_messages_handles_missing_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """When run standalone with no Phase 1 output yet (e.g. the phase-subgraph
    smoke test invoking Phase 3 in isolation), missing upstream `LabState`
    path fields must degrade to a placeholder message, not raise — mirrors
    `validation_strategist`'s handling of the same not-yet-available case."""
    _, workspace_instance = mock_workspace_manager
    node = BaselineDesignerNode()
    state = _build_state()
    state["problem_definition_path"] = ""
    state["eda_report_path"] = ""

    node(state)

    workspace_instance.read_json.assert_not_called()
    workspace_instance.read_text.assert_not_called()
    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "not yet available" in last_message.content


def test_build_messages_handles_unreadable_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    workspace_instance.read_text.side_effect = OSError("boom")
    node = BaselineDesignerNode()
    state = _build_state()

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "unable to read" in last_message.content


def test_empty_target_column_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_DESIGN, "target_column": ""}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = BaselineDesignerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="target_column"):
        node(state)
