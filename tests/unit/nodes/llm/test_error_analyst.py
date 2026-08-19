"""Unit tests for src/nodes/llm/error_analyst.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at both its `base.py` import location and its
`error_analyst.py` import location, matching `test_solution_architect.py`'s
convention). No network calls, no real filesystem writes.
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
from src.nodes.llm.error_analyst import ErrorAnalystNode
from src.state import new_state

EXPERIMENT_DIR = "experiments/exp_0"
SCORE_EVALUATION_PATH = "reports/score_evaluation_0.json"
FEATURE_IMPORTANCE_PATH = "reports/feature_importance_0.json"
RESULTS_PATH = f"{EXPERIMENT_DIR}/results.json"
DESIGN_PATH = f"{EXPERIMENT_DIR}/design.json"

SCORE_EVALUATION = {
    "iteration": 0,
    "experiment_dir": EXPERIMENT_DIR,
    "evaluated": True,
    "raw_score": 0.81,
    "normalized_score": 0.81,
    "direction": "maximize",
    "is_improvement": True,
    "best_score_before": None,
    "best_score_after": 0.81,
    "baseline_score": 0.78,
    "delta_vs_baseline": 0.03,
}
FEATURE_IMPORTANCE = {
    "skipped": False,
    "reason": None,
    "model_family": "lightgbm",
    "features": [{"feature": "f1", "importance": 9.0, "normalized_importance": 0.9, "rank": 1}],
}
EXPERIMENT_RESULTS = {"cv_score": 0.81, "fold_scores": [0.9, 0.72]}
EXPERIMENT_DESIGN = {"model_family": "lightgbm", "search_space": {}, "preprocessing": []}

VALID_DIAGNOSIS: dict[str, Any] = {
    "root_cause": "overfitting",
    "confidence": 0.7,
    "evidence": ["fold scores range from 0.72 to 0.90", "no regularization in fixed_params"],
    "recommended_focus": "regularization and capacity control",
}

_ARTIFACTS = {
    SCORE_EVALUATION_PATH: SCORE_EVALUATION,
    FEATURE_IMPORTANCE_PATH: FEATURE_IMPORTANCE,
    RESULTS_PATH: EXPERIMENT_RESULTS,
    DESIGN_PATH: EXPERIMENT_DESIGN,
}


def _diagnosis_response(**overrides: Any) -> str:
    data = copy.deepcopy(VALID_DIAGNOSIS)
    for key, value in overrides.items():
        if value is _REMOVED:
            data.pop(key, None)
        else:
            data[key] = value
    return json.dumps(data)


_REMOVED = object()


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=_diagnosis_response())
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
    own `__call__` writes the output through it) and `src.nodes.llm.error_analyst`
    (the node builds its own instance in `_build_messages` to read the four
    upstream artifacts) — both resolving to the same instance, since neither call
    site is aware of the other's `WorkspaceManager`."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.side_effect = lambda path: _artifact_for(path)
    instance.write_json.return_value = "/workspace/reports/error_diagnosis_0.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.error_analyst.WorkspaceManager") as mock_wm_cls_node,
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


def _read_paths(workspace_instance: MagicMock) -> list[str]:
    return [call.args[0] for call in workspace_instance.read_json.call_args_list]


def _human_message(mock_llm: MagicMock) -> str:
    return str(mock_llm.invoke.call_args[0][0][-1].content)


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("error_analyst")

    assert config.name == "error_analyst"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "reports/error_diagnosis_{iteration}.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("error_analyst", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — error_analyst" in prompt


def test_prompt_forbids_inventing_a_leaderboard_score() -> None:
    """No leaderboard score exists anywhere in this pipeline yet, so the prompt
    must say so rather than inviting the model to reason from one."""
    prompt = PromptLoader().load("error_analyst", "v1")

    assert "no leaderboard score anywhere" in prompt.lower()


def test_zero_arg_construction_succeeds(patched_llm_factory, patched_settings) -> None:
    node = ErrorAnalystNode()

    assert node.name == "error_analyst"


# -- the diagnosis artifact --


def test_call_writes_diagnosis_with_root_cause(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ErrorAnalystNode()

    node(_build_state())

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "reports/error_diagnosis_0.json"
    assert args[1]["root_cause"] == "overfitting"


def test_call_writes_full_validated_diagnosis(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ErrorAnalystNode()

    node(_build_state())

    artifact = _written_artifact(workspace_instance)
    assert artifact["iteration"] == 0
    assert artifact["confidence"] == 0.7
    assert artifact["evidence"] == VALID_DIAGNOSIS["evidence"]
    assert artifact["recommended_focus"] == VALID_DIAGNOSIS["recommended_focus"]
    assert artifact["inputs"] == {
        "score_evaluation": SCORE_EVALUATION_PATH,
        "feature_importance": FEATURE_IMPORTANCE_PATH,
        "experiment_results": RESULTS_PATH,
        "experiment_design": DESIGN_PATH,
    }


def test_call_writes_under_the_current_iteration_number(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ErrorAnalystNode()

    node(_build_state(current_iteration=3))

    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "reports/error_diagnosis_3.json"
    assert args[1]["iteration"] == 3


def test_extra_keys_in_the_response_are_dropped(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Whitelist rebuild: the LLM's own object is never written through."""
    _, workspace_instance = mock_workspace_manager
    mock_llm.invoke.return_value = AIMessage(
        content=_diagnosis_response(leaderboard_score=0.9, notes="ignore me")
    )
    node = ErrorAnalystNode()

    node(_build_state())

    artifact = _written_artifact(workspace_instance)
    assert set(artifact) == {
        "iteration",
        "root_cause",
        "confidence",
        "evidence",
        "recommended_focus",
        "inputs",
    }


def test_build_output_state_returns_no_lab_state_field(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`src/state.py` is a protected contract — this node adds no field to it,
    and in particular does not touch `current_iteration`."""
    node = ErrorAnalystNode()

    delta = node(_build_state())

    assert set(delta) == {"messages"}


# -- input wiring --


def test_reads_score_evaluation_and_feature_importance_for_this_iteration(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ErrorAnalystNode()

    node(_build_state())

    paths = _read_paths(workspace_instance)
    assert SCORE_EVALUATION_PATH in paths
    assert FEATURE_IMPORTANCE_PATH in paths


def test_joins_results_and_design_off_the_score_artifacts_experiment_dir(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """The experiment directory is taken from the score artifact, never
    re-derived from `current_iteration` or from `state["experiments"]` — here the
    two disagree deliberately."""
    _, workspace_instance = mock_workspace_manager
    divergent = copy.deepcopy(SCORE_EVALUATION) | {"experiment_dir": "experiments/exp_2"}
    workspace_instance.read_json.side_effect = lambda path: (
        divergent
        if path == SCORE_EVALUATION_PATH
        else {"joined": True}
        if path.startswith("experiments/exp_2/")
        else _artifact_for(path)
    )
    node = ErrorAnalystNode()

    node(_build_state())

    paths = _read_paths(workspace_instance)
    assert "experiments/exp_2/results.json" in paths
    assert "experiments/exp_2/design.json" in paths
    assert "experiments/exp_0/results.json" not in paths


def test_injects_all_four_sections_into_the_human_message(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    node = ErrorAnalystNode()

    node(_build_state())

    content = _human_message(mock_llm)
    assert "## Score evaluation" in content
    assert "## Feature importance" in content
    assert "## Experiment results" in content
    assert "## Experiment design" in content
    assert '"cv_score": 0.81' in content


# -- degradation --


def test_missing_score_artifact_degrades_to_a_placeholder(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    node = ErrorAnalystNode()

    node(_build_state())

    assert "(score evaluation not yet available)" in _human_message(mock_llm)
    assert _written_artifact(workspace_instance)["inputs"]["score_evaluation"] is None


def test_missing_experiment_artifacts_degrade_to_placeholders(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    node = ErrorAnalystNode()

    node(_build_state())

    content = _human_message(mock_llm)
    assert "(feature importance report not yet available)" in content
    assert "(experiment results not available)" in content
    assert "(experiment design not available)" in content


def test_recursion_error_reading_an_input_does_not_raise(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`RecursionError` is a `RuntimeError` — neither `OSError` nor `ValueError`
    would catch it, and a pathologically nested upstream artifact must not abort
    the graph run."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = RecursionError("too deep")
    node = ErrorAnalystNode()

    node(_build_state())

    workspace_instance.write_json.assert_called_once()


def test_traversal_experiment_dir_is_rejected_without_a_read_attempt(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    escaping = copy.deepcopy(SCORE_EVALUATION) | {"experiment_dir": "../etc"}
    workspace_instance.read_json.side_effect = lambda path: (
        escaping if path == SCORE_EVALUATION_PATH else _artifact_for(path)
    )
    node = ErrorAnalystNode()

    node(_build_state())

    assert not [p for p in _read_paths(workspace_instance) if p.startswith("..")]
    artifact = _written_artifact(workspace_instance)
    assert artifact["inputs"]["experiment_results"] is None
    assert artifact["inputs"]["experiment_design"] is None


def test_absolute_experiment_dir_is_rejected_without_a_read_attempt(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    escaping = copy.deepcopy(SCORE_EVALUATION) | {"experiment_dir": "/etc"}
    workspace_instance.read_json.side_effect = lambda path: (
        escaping if path == SCORE_EVALUATION_PATH else _artifact_for(path)
    )
    node = ErrorAnalystNode()

    node(_build_state())

    assert not [p for p in _read_paths(workspace_instance) if p.startswith("/etc")]
    assert _written_artifact(workspace_instance)["inputs"]["experiment_results"] is None


# -- response shapes --


def test_fenced_json_response_is_parsed(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    mock_llm.invoke.return_value = AIMessage(content=f"```json\n{_diagnosis_response()}\n```")
    node = ErrorAnalystNode()

    node(_build_state())

    assert _written_artifact(workspace_instance)["root_cause"] == "overfitting"


def test_prose_wrapped_json_response_is_salvaged(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """Phase 6 declares `critic: null`, so there is no retry wrapper — a stray
    sentence must not abort the run."""
    _, workspace_instance = mock_workspace_manager
    mock_llm.invoke.return_value = AIMessage(
        content=f"Here is the diagnosis:\n{_diagnosis_response()}\nHope that helps."
    )
    node = ErrorAnalystNode()

    node(_build_state())

    assert _written_artifact(workspace_instance)["root_cause"] == "overfitting"


# -- validation rejects --


@pytest.mark.parametrize("field", ["root_cause", "confidence", "evidence", "recommended_focus"])
def test_missing_required_field_is_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock, field: str
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_diagnosis_response(**{field: _REMOVED}))
    node = ErrorAnalystNode()

    with pytest.raises(ValueError, match="error_analyst"):
        node(_build_state())


@pytest.mark.parametrize("root_cause", ["wrong family", "unknown", "", None])
def test_root_cause_outside_the_vocabulary_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    root_cause: Any,
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_diagnosis_response(root_cause=root_cause))
    node = ErrorAnalystNode()

    with pytest.raises(ValueError, match="root_cause"):
        node(_build_state())


@pytest.mark.parametrize("confidence", [1.5, -0.1, True, "0.7"])
def test_confidence_outside_the_unit_interval_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    confidence: Any,
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_diagnosis_response(confidence=confidence))
    node = ErrorAnalystNode()

    with pytest.raises(ValueError, match="confidence"):
        node(_build_state())


@pytest.mark.parametrize("evidence", [[], ["ok", 3], ["  "], ["e"] * 9, "not a list"])
def test_malformed_evidence_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    mock_llm: MagicMock,
    evidence: Any,
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_diagnosis_response(evidence=evidence))
    node = ErrorAnalystNode()

    with pytest.raises(ValueError, match="evidence"):
        node(_build_state())


def test_blank_recommended_focus_is_rejected(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_diagnosis_response(recommended_focus="   "))
    node = ErrorAnalystNode()

    with pytest.raises(ValueError, match="recommended_focus"):
        node(_build_state())


def test_unparseable_response_is_rejected_naming_the_node(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content="I could not analyze this.")
    node = ErrorAnalystNode()

    with pytest.raises(ValueError, match="error_analyst"):
        node(_build_state())


def test_non_integer_current_iteration_coerces_to_zero_in_the_output_path(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """`isinstance(True, int)` is `True`, so an unguarded read would file the
    diagnosis at `reports/error_diagnosis_True.json` next to an
    `"iteration": 0` body."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["current_iteration"] = True
    node = ErrorAnalystNode()

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "reports/error_diagnosis_0.json"
    assert args[1]["iteration"] == 0
