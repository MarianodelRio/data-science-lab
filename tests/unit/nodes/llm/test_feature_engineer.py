"""Unit tests for src/nodes/llm/feature_engineer.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at their import location inside
`src.nodes.llm.base`, matching `test_baseline_designer.py`'s convention). No
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
from src.nodes.llm.feature_engineer import FeatureEngineerNode
from src.state import new_state

VALID_SPEC = {
    "encodings": [
        {"column": "cat1", "method": "one_hot"},
        {"column": "cat2", "method": "target_encoding", "fold_aware": True},
    ],
    "null_handling": [{"column": "num1", "strategy": "median_impute"}],
    "interactions": [{"columns": ["num1", "num2"], "type": "multiply"}],
}
RESPONSE_CONTENT = json.dumps(VALID_SPEC)
SOLUTION_PLAN = {
    "approach": "gradient_boosting",
    "candidate_models": ["lightgbm"],
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
    `src.nodes.llm.feature_engineer` (the node's own instance, constructed
    in its `_build_messages` override to read the upstream artifacts) —
    both must resolve to the same mock instance since neither call site is
    aware of the other's WorkspaceManager."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.return_value = SOLUTION_PLAN
    instance.read_text.return_value = EDA_REPORT_TEXT
    instance.write_json.return_value = "/workspace/design/iteration_0/feature_spec.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.feature_engineer.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    state["solution_plan_path"] = "design/solution_plan.json"
    state["eda_report_path"] = "reports/eda_report.md"
    return state


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("feature_engineer")

    assert config.name == "feature_engineer"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "design/iteration_{iteration}/feature_spec.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("feature_engineer", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — feature_engineer" in prompt


# -- __call__ behavior --


def test_call_writes_spec_via_workspace_write_json(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "design/iteration_0/feature_spec.json"
    assert args[1] == VALID_SPEC


def test_call_sets_feature_spec_path(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    delta = node(state)

    assert delta["feature_spec_path"] == workspace_instance.write_json.return_value


def test_call_reads_solution_plan_and_eda_report(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    workspace_instance.read_json.assert_called_once_with("design/solution_plan.json")
    workspace_instance.read_text.assert_called_once_with("reports/eda_report.md")


def test_build_messages_includes_both_sections(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "## Solution plan" in last_message.content
    assert "gradient_boosting" in last_message.content
    assert "## EDA report" in last_message.content
    assert EDA_REPORT_TEXT in last_message.content


def test_relative_to_workspace_converts_absolute_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()
    state["solution_plan_path"] = "/workspace/design/solution_plan.json"
    state["eda_report_path"] = "/workspace/reports/eda_report.md"

    node(state)

    workspace_instance.read_json.assert_called_once_with("design/solution_plan.json")
    workspace_instance.read_text.assert_called_once_with("reports/eda_report.md")


def test_json_in_json_fence_is_parsed(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=f"```json\n{RESPONSE_CONTENT}\n```")
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1] == VALID_SPEC


# -- missing/unreadable upstream artifacts --


def test_build_messages_handles_missing_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """When run standalone with no T-021 (solution_architect) output yet, missing
    upstream `LabState` path fields must degrade to a placeholder message, not
    raise — mirrors `baseline_designer`'s handling of the same not-yet-available
    case."""
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()
    state["solution_plan_path"] = ""
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
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "unable to read" in last_message.content


# -- JSON parsing errors --


def test_invalid_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="feature_engineer"):
        node(state)


def test_non_dict_top_level_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps([VALID_SPEC]))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="feature_engineer"):
        node(state)


# -- schema validation --


@pytest.mark.parametrize("missing_field", ["encodings", "null_handling", "interactions"])
def test_missing_required_list_field_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, missing_field: str
) -> None:
    data = dict(VALID_SPEC)
    del data[missing_field]
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match=missing_field):
        node(state)


def test_encodings_wrong_type_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_SPEC, "encodings": "not a list"}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="encodings"):
        node(state)


@pytest.mark.parametrize("missing_field", ["column", "method"])
def test_encoding_missing_column_or_method_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, missing_field: str
) -> None:
    entry = {"column": "cat1", "method": "one_hot"}
    del entry[missing_field]
    data = {**VALID_SPEC, "encodings": [entry]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match=missing_field):
        node(state)


def test_target_encoding_without_fold_aware_key_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_SPEC, "encodings": [{"column": "cat1", "method": "target_encoding"}]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="fold_aware"):
        node(state)


def test_target_encoding_with_fold_aware_false_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {
        **VALID_SPEC,
        "encodings": [{"column": "cat1", "method": "target_encoding", "fold_aware": False}],
    }
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="fold_aware"):
        node(state)


def test_target_encoding_with_fold_aware_true_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {
        **VALID_SPEC,
        "encodings": [{"column": "cat1", "method": "target_encoding", "fold_aware": True}],
    }
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1]["encodings"] == [
        {"column": "cat1", "method": "target_encoding", "fold_aware": True}
    ]


def test_non_target_encoding_does_not_require_fold_aware(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_SPEC, "encodings": [{"column": "cat1", "method": "one_hot"}]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1]["encodings"] == [{"column": "cat1", "method": "one_hot"}]


# -- target-encoding-family keyword matching (not just the literal 'target_encoding' string) --


@pytest.mark.parametrize(
    "method",
    [
        "mean_encoding",
        "leave_one_out",
        "leave-one-out",
        "WOE",
        "weight of evidence",
        "CatBoost encoding",
        "James-Stein encoding",
        "M-estimate encoding",
        "impact_encoding",
        "target_mean",
        "smoothed target encoding",
    ],
)
def test_target_encoding_family_synonym_requires_fold_aware(
    patched_llm_factory, patched_settings, mock_workspace_manager, method: str
) -> None:
    """category_encoders-library names for the target-encoding family (mean encoding,
    leave-one-out, weight-of-evidence, CatBoost, James-Stein, M-estimate, impact
    encoding, ...) never contain the literal substring 'target' (except the target_mean/
    smoothed-target variants), so a bare substring check on 'target' would silently let
    a leaky, non-fold-aware version of any of these through. All must require
    'fold_aware': true exactly like 'target_encoding' does."""
    data = {**VALID_SPEC, "encodings": [{"column": "cat1", "method": method}]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="fold_aware"):
        node(state)


def test_target_encoding_family_synonym_with_fold_aware_true_is_accepted(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {
        **VALID_SPEC,
        "encodings": [{"column": "cat1", "method": "leave_one_out", "fold_aware": True}],
    }
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1]["encodings"] == [
        {"column": "cat1", "method": "leave_one_out", "fold_aware": True}
    ]


def test_method_merely_mentioning_target_does_not_require_fold_aware(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """False-positive regression: a method name that mentions the word 'target' without
    being a target-encoding-family technique (e.g. explicitly excluding a target-related
    leak) must not be forced to declare 'fold_aware' — only the curated whole-phrase
    keywords should trigger the requirement, not a bare 'target' substring."""
    data = {
        **VALID_SPEC,
        "encodings": [{"column": "cat1", "method": "frequency_encoding_excluding_target_leak"}],
    }
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    _, workspace_instance = mock_workspace_manager
    node = FeatureEngineerNode()
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1]["encodings"] == [
        {"column": "cat1", "method": "frequency_encoding_excluding_target_leak"}
    ]


# -- not-a-dict item guards --


def test_encoding_item_not_a_dict_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_SPEC, "encodings": ["not a dict"]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="must be an object"):
        node(state)


def test_null_handling_item_not_a_dict_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_SPEC, "null_handling": ["not a dict"]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="must be an object"):
        node(state)


def test_interactions_item_not_a_dict_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_SPEC, "interactions": ["not a dict"]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="must be an object"):
        node(state)


def test_null_handling_missing_column_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_SPEC, "null_handling": [{"strategy": "median_impute"}]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="column"):
        node(state)


def test_interactions_missing_type_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_SPEC, "interactions": [{"columns": ["num1", "num2"]}]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="type"):
        node(state)


def test_interactions_columns_requires_at_least_two(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_SPEC, "interactions": [{"columns": ["only_one"], "type": "multiply"}]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="columns"):
        node(state)


def test_null_handling_missing_strategy_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_SPEC, "null_handling": [{"column": "num1"}]}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = FeatureEngineerNode()
    state = _build_state()

    with pytest.raises(ValueError, match="strategy"):
        node(state)
