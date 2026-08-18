"""Unit tests for src/nodes/llm/experiment_designer.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at both its `base.py` and its
`experiment_designer.py` import locations). No network calls, no real
filesystem writes.

This node is the pipeline's **only** `current_iteration` writer, so the
increment and the pre-increment artifact number are both asserted directly
here — see the module docstring of the node under test.
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
from src.nodes.llm.experiment_designer import ExperimentDesignerNode
from src.state import new_state

HYPOTHESES_PATH = "reports/hypotheses_0.json"
DIAGNOSIS_PATH = "reports/error_diagnosis_0.json"

HYPOTHESES = {
    "iteration": 0,
    "hypotheses": [
        {
            "id": "stronger_regularization",
            "statement": "Raising L2 regularization will close the fold-score gap.",
            "rationale": "Fold variance dominates the diagnosis.",
            "priority": 1,
            "expected_impact": "high",
            "addresses_root_cause": "overfitting",
        }
    ],
    "rag_query": "overfitting remedies and previously tried approaches for comp",
    "prior_attempts_considered": 1,
}
ERROR_DIAGNOSIS = {
    "iteration": 0,
    "root_cause": "overfitting",
    "confidence": 0.7,
    "evidence": ["fold scores range from 0.72 to 0.90"],
    "recommended_focus": "regularization",
}

VALID_PLAN: dict[str, Any] = {
    "changes": [
        {
            "order": 1,
            "change": "Widen the L2 regularization range in the search space.",
            "target": "experiment_design",
            "hypothesis_id": "stronger_regularization",
            "expected_effect": "Fold-score spread narrows below 0.05.",
        },
        {
            "order": 2,
            "change": "Drop the two near-zero-importance interaction features.",
            "target": "feature_spec",
            "hypothesis_id": "stronger_regularization",
            "expected_effect": "Slightly higher CV score with less variance.",
        },
    ],
    "rationale": "Attack the variance first, then trim noise features.",
}

_REMOVED = object()

_ARTIFACTS = {HYPOTHESES_PATH: HYPOTHESES, DIAGNOSIS_PATH: ERROR_DIAGNOSIS}


def _plan_response(**overrides: Any) -> str:
    data = copy.deepcopy(VALID_PLAN)
    for key, value in overrides.items():
        if value is _REMOVED:
            data.pop(key, None)
        else:
            data[key] = value
    return json.dumps(data)


def _change(**overrides: Any) -> dict[str, Any]:
    entry = copy.deepcopy(VALID_PLAN["changes"][0])
    entry.update(overrides)
    return entry


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=_plan_response())
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
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.side_effect = lambda path: _artifact_for(path)
    instance.write_json.return_value = "/workspace/reports/experiment_plan_0.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.experiment_designer.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


def _artifact_for(path: str) -> dict[str, Any]:
    if path not in _ARTIFACTS:
        raise OSError(f"no such artifact: {path}")
    return copy.deepcopy(_ARTIFACTS[path])


def _build_state(current_iteration: int = 0) -> Any:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    return state


def _written_artifact(workspace_instance: MagicMock) -> dict[str, Any]:
    args, _ = workspace_instance.write_json.call_args
    return args[1]


def _written_path(workspace_instance: MagicMock) -> str:
    args, _ = workspace_instance.write_json.call_args
    return str(args[0])


def _read_paths(workspace_instance: MagicMock) -> list[str]:
    return [call.args[0] for call in workspace_instance.read_json.call_args_list]


def _human_message(mock_llm: MagicMock) -> str:
    return str(mock_llm.invoke.call_args[0][0][-1].content)


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("experiment_designer")

    assert config.name == "experiment_designer"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "reports/experiment_plan_{iteration}.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("experiment_designer", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — experiment_designer" in prompt


def test_zero_arg_construction_succeeds(patched_llm_factory, patched_settings) -> None:
    node = ExperimentDesignerNode()

    assert node.name == "experiment_designer"


# -- the plan artifact --


def test_writes_plan_with_an_ordered_list_of_changes(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Ordering is a property of the artifact, not of the response order."""
    _, workspace_instance = mock_workspace_manager
    mock_llm.invoke.return_value = AIMessage(
        content=_plan_response(
            changes=[
                _change(order=2, change="second"),
                _change(order=1, change="first"),
                _change(order=3, change="third"),
            ]
        )
    )
    node = ExperimentDesignerNode()

    node(_build_state())

    changes = _written_artifact(workspace_instance)["changes"]
    assert [c["order"] for c in changes] == [1, 2, 3]
    assert [c["change"] for c in changes] == ["first", "second", "third"]


def test_writes_rationale_and_iteration_fields(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ExperimentDesignerNode()

    node(_build_state())

    artifact = _written_artifact(workspace_instance)
    assert artifact["rationale"] == VALID_PLAN["rationale"]
    assert artifact["iteration"] == 0
    assert artifact["next_iteration"] == 1


def test_extra_keys_in_a_change_are_dropped(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    mock_llm.invoke.return_value = AIMessage(
        content=_plan_response(changes=[_change(estimated_hours=3)])
    )
    node = ExperimentDesignerNode()

    node(_build_state())

    change = _written_artifact(workspace_instance)["changes"][0]
    assert set(change) == {"order", "change", "target", "hypothesis_id", "expected_effect"}


# -- the increment (adjustment 1) --


def test_increments_current_iteration_exactly_once(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """The only `current_iteration` write anywhere in `src/`: one increment per
    completed Phase 6 pass, and no other `LabState` field touched."""
    node = ExperimentDesignerNode()

    delta = node(_build_state(current_iteration=3))

    assert delta["current_iteration"] == 4
    assert set(delta) == {"messages", "current_iteration"}


def test_artifact_lands_under_the_pre_increment_number(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`LLMNode.__call__` resolves the output path before `_build_output_state`
    runs, so this node's own plan stays aligned with the `exp_{N}` directory just
    scored — and with the four earlier Phase 6 artifacts."""
    _, workspace_instance = mock_workspace_manager
    node = ExperimentDesignerNode()

    delta = node(_build_state(current_iteration=3))

    assert _written_path(workspace_instance) == "reports/experiment_plan_3.json"
    artifact = _written_artifact(workspace_instance)
    assert artifact["iteration"] == 3
    assert artifact["next_iteration"] == 4
    assert delta["current_iteration"] == 4


def test_increments_from_zero_on_the_first_pass(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ExperimentDesignerNode()

    delta = node(_build_state(current_iteration=0))

    assert delta["current_iteration"] == 1
    assert _written_path(workspace_instance) == "reports/experiment_plan_0.json"


def test_non_integer_current_iteration_coerces_to_zero(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`isinstance(True, int)` is `True`, so an unguarded read would file the
    plan at `reports/experiment_plan_True.json` and increment to `2`."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["current_iteration"] = True
    node = ExperimentDesignerNode()

    delta = node(state)

    assert _written_path(workspace_instance) == "reports/experiment_plan_0.json"
    assert delta["current_iteration"] == 1


# -- input wiring and degradation --


def test_reads_hypotheses_and_diagnosis_for_this_iteration(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ExperimentDesignerNode()

    node(_build_state())

    paths = _read_paths(workspace_instance)
    assert HYPOTHESES_PATH in paths
    assert DIAGNOSIS_PATH in paths
    content = _human_message(mock_llm)
    assert "## Hypotheses" in content
    assert "## Error diagnosis" in content
    assert "stronger_regularization" in content


def test_missing_inputs_degrade_to_placeholders(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    node = ExperimentDesignerNode()

    delta = node(_build_state())

    content = _human_message(mock_llm)
    assert "(hypotheses not yet available)" in content
    assert "(error diagnosis not yet available)" in content
    workspace_instance.write_json.assert_called_once()
    assert delta["current_iteration"] == 1


def test_prose_wrapped_json_response_is_salvaged(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    mock_llm.invoke.return_value = AIMessage(
        content=f"Here is the plan:\n{_plan_response()}\nGood luck."
    )
    node = ExperimentDesignerNode()

    node(_build_state())

    assert len(_written_artifact(workspace_instance)["changes"]) == 2


# -- validation rejects --


@pytest.mark.parametrize("changes", [[], "not a list", None, [_change()] * 7])
def test_malformed_changes_list_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    changes: Any,
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_plan_response(changes=changes))
    node = ExperimentDesignerNode()

    with pytest.raises(ValueError, match="experiment_designer"):
        node(_build_state())


@pytest.mark.parametrize(
    "orders", [[1, 3], [1, 1], [0, 1], [2, 3]], ids=["gap", "duplicate", "zero", "offset"]
)
def test_orders_not_covering_one_to_n_are_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    orders: list[int],
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_plan_response(changes=[_change(order=o) for o in orders])
    )
    node = ExperimentDesignerNode()

    with pytest.raises(ValueError, match="order"):
        node(_build_state())


@pytest.mark.parametrize("target", ["solution plan", "SOLUTION_PLAN", "notebook", None])
def test_target_outside_the_enum_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    target: Any,
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_plan_response(changes=[_change(target=target)])
    )
    node = ExperimentDesignerNode()

    with pytest.raises(ValueError, match="target"):
        node(_build_state())


@pytest.mark.parametrize("field", ["change", "hypothesis_id", "expected_effect"])
def test_blank_change_fields_are_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    field: str,
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_plan_response(changes=[_change(**{field: "   "})])
    )
    node = ExperimentDesignerNode()

    with pytest.raises(ValueError, match=field):
        node(_build_state())


@pytest.mark.parametrize("rationale", [_REMOVED, "", "   ", None, 7])
def test_missing_or_blank_rationale_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    rationale: Any,
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_plan_response(rationale=rationale))
    node = ExperimentDesignerNode()

    with pytest.raises(ValueError, match="rationale"):
        node(_build_state())


def test_no_artifact_is_written_and_no_increment_happens_on_a_rejected_response(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """A rejected plan must not advance the iteration counter — the increment
    lives in `_build_output_state`, which `LLMNode.__call__` never reaches once
    `_write_output` raises."""
    _, workspace_instance = mock_workspace_manager
    mock_llm.invoke.return_value = AIMessage(content="I could not produce a plan.")
    node = ExperimentDesignerNode()

    with pytest.raises(ValueError, match="experiment_designer"):
        node(_build_state())

    workspace_instance.write_json.assert_not_called()
