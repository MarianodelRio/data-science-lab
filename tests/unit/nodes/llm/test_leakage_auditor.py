"""Unit tests for src/nodes/llm/leakage_auditor.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at their import location inside
`src.nodes.llm.base`, matching `test_data_analyst.py`'s convention). No
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
from src.nodes.llm.leakage_auditor import LeakageAuditorNode
from src.state import new_state

VALID_AUDIT = {
    "leaks": [{"description": "id column correlated with target", "severity": "high"}],
    "severity": "high",
    "blocks_progression": True,
}
RESPONSE_CONTENT = json.dumps(VALID_AUDIT)
EDA_REPORT_TEXT = "# EDA Report\n\nOne file found: `data/raw/train.csv`."
PROBLEM_DEFINITION = {
    "problem_type": "binary_classification",
    "success_metric": "roc_auc",
    "constraints": [],
}


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
    `src.nodes.llm.leakage_auditor` (the node's own instance, constructed in
    its `_build_messages` override to read the EDA report + problem
    definition) — both must resolve to the same mock instance since neither
    call site is aware of the other's WorkspaceManager."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_text.return_value = EDA_REPORT_TEXT
    instance.read_json.return_value = PROBLEM_DEFINITION
    instance.write_json.return_value = "/workspace/reports/leakage_audit.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.leakage_auditor.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    state["eda_report_path"] = "reports/eda_report.md"
    state["problem_definition_path"] = "reports/problem_definition.json"
    return state


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("leakage_auditor")

    assert config.name == "leakage_auditor"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "reports/leakage_audit.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("leakage_auditor", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — leakage_auditor" in prompt


# -- __call__ behavior --


def test_call_writes_leakage_audit_via_workspace_write_json(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = LeakageAuditorNode()
    state = _build_state()

    node(state)

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "reports/leakage_audit.json"
    assert args[1] == VALID_AUDIT


def test_call_reads_eda_report_and_problem_definition(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = LeakageAuditorNode()
    state = _build_state()

    node(state)

    workspace_instance.read_text.assert_called_once_with("reports/eda_report.md")
    workspace_instance.read_json.assert_called_once_with("reports/problem_definition.json")


def test_call_state_delta_is_messages_only(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """Regression guard: leakage_auditor has no LabState path field of its
    own (see src/state.py — no leakage-related field exists), so its delta
    must never grow a path key beyond the base LLMNode's 'messages'."""
    node = LeakageAuditorNode()
    state = _build_state()

    delta = node(state)

    assert set(delta.keys()) == {"messages"}


@pytest.mark.parametrize("blocks_progression", [True, False])
def test_blocks_progression_roundtrips(
    patched_llm_factory, patched_settings, mock_workspace_manager, blocks_progression: bool
) -> None:
    data = {**VALID_AUDIT, "blocks_progression": blocks_progression}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    _, workspace_instance = mock_workspace_manager
    node = LeakageAuditorNode()
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1]["blocks_progression"] is blocks_progression


def test_multiple_fences_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(
        content=f"```json\n{RESPONSE_CONTENT}\n```\n\n```json\n{{}}\n```"
    )
    node = LeakageAuditorNode()
    state = _build_state()

    with pytest.raises(ValueError, match="leakage_auditor"):
        node(state)


def test_invalid_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    node = LeakageAuditorNode()
    state = _build_state()

    with pytest.raises(ValueError, match="leakage_auditor"):
        node(state)


@pytest.mark.parametrize(
    "missing_field",
    ["leaks", "severity", "blocks_progression"],
)
def test_missing_required_field_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, missing_field: str
) -> None:
    data = dict(VALID_AUDIT)
    del data[missing_field]
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = LeakageAuditorNode()
    state = _build_state()

    with pytest.raises(ValueError, match=missing_field):
        node(state)


def test_blocks_progression_string_type_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_AUDIT, "blocks_progression": "true"}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = LeakageAuditorNode()
    state = _build_state()

    with pytest.raises(ValueError, match="blocks_progression"):
        node(state)


def test_leaks_wrong_type_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = {**VALID_AUDIT, "leaks": "not a list"}
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    node = LeakageAuditorNode()
    state = _build_state()

    with pytest.raises(ValueError, match="leaks"):
        node(state)
