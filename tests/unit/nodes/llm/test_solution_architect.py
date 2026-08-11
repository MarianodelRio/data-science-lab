"""Unit tests for src/nodes/llm/solution_architect.py.

All external calls are mocked: `LLMFactory`/the LLM itself, `WorkspaceManager`
(patched at both its `base.py` import location and its
`solution_architect.py` import location, matching `test_baseline_designer.py`'s
convention), and `RagStore` (injected directly, matching
`test_web_researcher.py`'s convention). No network calls, no real filesystem
writes.
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
from src.nodes.llm.solution_architect import SolutionArchitectNode
from src.state import new_state
from src.tools.rag import IndexDocument

FAKE_INDEX_DOCUMENT = IndexDocument(
    id="doc-1",
    text="A blog post describing a stacking ensemble that won a tabular competition.",
    source="kaggle-forum-post-1",
    problem_type=["binary_classification"],
    methods_used=["lightgbm", "stacking"],
    dataset_characteristics=["tabular", "imbalanced"],
    key_findings="Stacking improved AUC by 2pts.",
    relevance_score=0.8,
)
VALID_SOLUTION_PLAN = {
    "model_families": ["lightgbm", "xgboost", "catboost"],
    "order": ["xgboost", "lightgbm", "catboost"],
    "ensembling_strategy": "Weighted average of OOF predictions from all three models.",
    "realistic_ceiling": {
        "metric": "roc_auc",
        "target_score": 0.91,
        "rationale": "RAG findings report similar competitions reaching ~0.91 with stacking.",
    },
    "rationale": "Try three strong gradient boosting families in order of RAG relevance.",
}
RESPONSE_CONTENT = json.dumps(VALID_SOLUTION_PLAN)
BASELINE_RESULTS = {
    "cv_score": 0.85,
    "fold_scores": [0.84, 0.86],
    "model_class": "LGBMClassifier",
    "design": {"model": "lightgbm"},
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
    `src.nodes.llm.solution_architect` (the node's own instance, constructed
    in its `_build_messages` override to read the baseline results) — both
    must resolve to the same mock instance since neither call site is aware
    of the other's WorkspaceManager."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.return_value = BASELINE_RESULTS
    instance.write_json.return_value = "/workspace/design/iteration_0/solution_plan.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.solution_architect.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    state["baseline_results_path"] = "experiments/baseline/results.json"
    return state


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("solution_architect")

    assert config.name == "solution_architect"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "design/iteration_{iteration}/solution_plan.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("solution_architect", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — solution_architect" in prompt


# -- zero-arg construction --


def test_zero_arg_construction_succeeds_with_no_rag_store(
    patched_llm_factory, patched_settings
) -> None:
    node = SolutionArchitectNode()

    assert node._rag_store is None


# -- __call__ behavior --


def test_call_writes_solution_plan_iteration_0(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state(current_iteration=0)

    node(state)

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "design/iteration_0/solution_plan.json"
    assert args[1] == VALID_SOLUTION_PLAN


def test_call_writes_solution_plan_iteration_1(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.write_json.return_value = "/workspace/design/iteration_1/solution_plan.json"
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state(current_iteration=1)

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "design/iteration_1/solution_plan.json"
    assert args[1] == VALID_SOLUTION_PLAN


def test_call_sets_solution_plan_path_state(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    delta = node(state)

    assert delta["solution_plan_path"] == workspace_instance.write_json.return_value


def test_build_messages_queries_rag_store(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    rag_store.query.assert_called_once()
    args, _ = rag_store.query.call_args
    assert state["competition_name"] in args[0]


def test_build_messages_includes_rag_findings(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "## RAG findings" in last_message.content
    assert FAKE_INDEX_DOCUMENT.key_findings in last_message.content


def test_build_messages_handles_empty_rag_results(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    rag_store = MagicMock()
    rag_store.query.return_value = []
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "No relevant findings found in the RAG store." in last_message.content


def test_build_messages_includes_baseline_results(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.return_value = BASELINE_RESULTS
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "## Baseline results" in last_message.content
    assert "0.85" in last_message.content


def test_build_messages_handles_missing_baseline_results_path(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()
    state["baseline_results_path"] = ""

    node(state)

    workspace_instance.read_json.assert_not_called()
    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "not yet available" in last_message.content


def test_build_messages_handles_unreadable_baseline_results_path(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert "unable to read" in last_message.content


def test_relative_to_workspace_converts_absolute_baseline_path(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()
    state["baseline_results_path"] = "/workspace/experiments/baseline/results.json"

    node(state)

    workspace_instance.read_json.assert_called_once_with("experiments/baseline/results.json")


def test_json_in_json_fence_is_parsed(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=f"```json\n{RESPONSE_CONTENT}\n```")
    _, workspace_instance = mock_workspace_manager
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[1] == VALID_SOLUTION_PLAN


def test_invalid_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="solution_architect"):
        node(state)


def test_non_dict_top_level_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps([VALID_SOLUTION_PLAN]))
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="solution_architect"):
        node(state)


@pytest.mark.parametrize(
    "missing_field",
    ["model_families", "order", "ensembling_strategy", "realistic_ceiling", "rationale"],
)
def test_missing_required_key_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, missing_field: str
) -> None:
    data = json.loads(json.dumps(VALID_SOLUTION_PLAN))
    del data[missing_field]
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match=missing_field):
        node(state)


def test_order_not_matching_model_families_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = json.loads(json.dumps(VALID_SOLUTION_PLAN))
    data["order"] = ["lightgbm", "random_forest", "catboost"]
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="order"):
        node(state)


def test_order_duplicate_entries_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = json.loads(json.dumps(VALID_SOLUTION_PLAN))
    data["order"] = ["lightgbm", "lightgbm", "xgboost"]
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="duplicate"):
        node(state)


def test_model_families_duplicate_entries_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    data = json.loads(json.dumps(VALID_SOLUTION_PLAN))
    data["model_families"] = ["lightgbm", "lightgbm", "catboost"]
    data["order"] = ["lightgbm", "lightgbm", "catboost"]
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="duplicate"):
        node(state)


@pytest.mark.parametrize("missing_subfield", ["metric", "target_score", "rationale"])
def test_realistic_ceiling_missing_subfield_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, missing_subfield: str
) -> None:
    data = json.loads(json.dumps(VALID_SOLUTION_PLAN))
    del data["realistic_ceiling"][missing_subfield]
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="realistic_ceiling"):
        node(state)


@pytest.mark.parametrize("bad_target_score", ["not-a-number", True, None])
def test_realistic_ceiling_target_score_wrong_type_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, bad_target_score: Any
) -> None:
    data = json.loads(json.dumps(VALID_SOLUTION_PLAN))
    data["realistic_ceiling"]["target_score"] = bad_target_score
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(data))
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="target_score"):
        node(state)


def test_call_does_not_read_problem_definition_path(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """Locks in the v1 descope: no dedicated read of `problem_definition_path`
    occurs — `state['problem_definition_path']` is left unset entirely and the
    node must not attempt to read it (its only inputs are RAG findings and the
    baseline results)."""
    _, workspace_instance = mock_workspace_manager
    rag_store = MagicMock()
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT]
    node = SolutionArchitectNode(rag_store=rag_store)
    state = _build_state()
    assert "problem_definition_path" not in state or not state.get("problem_definition_path")

    node(state)

    for call in workspace_instance.read_json.call_args_list:
        assert "problem_definition" not in call.args[0]
