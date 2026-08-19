"""Unit tests for src/nodes/llm/report_writer.py.

All external calls are mocked: `LLMFactory`/the LLM itself and
`WorkspaceManager` (patched at both its `base.py` import location and its
`report_writer.py` import location, matching `test_error_analyst.py`'s
convention). No network calls, no real filesystem writes.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import src.nodes.llm.report_writer as report_writer_module
from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings
from src.graph.node_resolver import resolve_node
from src.nodes.llm import _delivery_common as common
from src.nodes.llm.report_writer import ReportWriterNode
from src.state import new_state

REPORT_PATH = "reports/final_report.md"
CODE_REVIEW_PATH = "reports/code_review.md"

_REPORT_MARKDOWN = (
    "# Final Report — comp\n\n"
    "## What was tried\n\nGradient boosting over five frozen folds.\n\n"
    "## What worked\n\nNothing beat the baseline.\n"
)

_PROBLEM_DEFINITION = {"problem_type": "binary_classification", "success_metric": "roc_auc"}
_SCORE_EVALUATION = {"raw_score": 0.81, "direction": "maximize", "is_improvement": False}
_ERROR_DIAGNOSIS = {"root_cause": "overfitting", "confidence": 0.6}
_HYPOTHESES = {"hypotheses": [{"id": "h1", "priority": 1}], "prior_attempts_considered": 0}


def _default_json_artifacts(iteration: int = 2) -> dict[str, Any]:
    return {
        "reports/problem_definition.json": _PROBLEM_DEFINITION,
        f"reports/score_evaluation_{iteration}.json": _SCORE_EVALUATION,
        f"reports/error_diagnosis_{iteration}.json": _ERROR_DIAGNOSIS,
        f"reports/hypotheses_{iteration}.json": _HYPOTHESES,
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
    llm.invoke.return_value = AIMessage(content=_REPORT_MARKDOWN)
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


class _Workspace:
    """Fake standing in for both `WorkspaceManager` instances. `workspace_path`
    is a real `Path` so path relativization behaves."""

    def __init__(self, json_artifacts: dict[str, Any], texts: dict[str, Any]) -> None:
        self.workspace_path = Path("/workspace")
        self.json_artifacts = json_artifacts
        self.texts = texts
        self.read_json_paths: list[str] = []
        self.read_text_paths: list[str] = []
        self.written: list[tuple[str, str]] = []
        self.write_json = MagicMock()

    def read_json(self, relative_path: str) -> Any:
        self.read_json_paths.append(relative_path)
        payload = self.json_artifacts.get(relative_path)
        if payload is None:
            raise OSError(f"no such artifact: {relative_path}")
        if isinstance(payload, Exception):
            raise payload
        return copy.deepcopy(payload)

    def read_text(self, relative_path: str) -> str:
        self.read_text_paths.append(relative_path)
        payload = self.texts.get(relative_path)
        if payload is None:
            raise OSError(f"no such file: {relative_path}")
        if isinstance(payload, Exception):
            raise payload
        return payload

    def write_text(self, relative_path: str, content: str) -> str:
        self.written.append((relative_path, content))
        return f"/workspace/{relative_path}"


@pytest.fixture
def json_artifacts() -> dict[str, Any]:
    return _default_json_artifacts()


@pytest.fixture
def texts() -> dict[str, Any]:
    return {CODE_REVIEW_PATH: "## Verdict\n\nclean\n"}


@pytest.fixture
def mock_workspace_manager(json_artifacts: dict[str, Any], texts: dict[str, Any]):
    instance = _Workspace(json_artifacts, texts)
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.report_writer.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield instance


def _build_state(current_iteration: int = 3, **overrides: Any) -> Any:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    for key, value in overrides.items():
        state[key] = value  # type: ignore[literal-required]
    return state


def _injected_message(mock_llm: MagicMock) -> str:
    messages = mock_llm.invoke.call_args[0][0]
    human = [m for m in messages if isinstance(m, HumanMessage)]
    assert len(human) == 1
    return str(human[0].content)


# -- config / prompt ------------------------------------------------------


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("report_writer")

    assert config.name == "report_writer"
    assert config.model_role == "research"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == REPORT_PATH
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("report_writer", config.prompt_version)
    assert prompt.startswith("# System prompt — report_writer")


def test_output_pattern_has_no_iteration_placeholder() -> None:
    assert "{iteration}" not in load_agent_config("report_writer").output_file_pattern


def test_output_pattern_matches_the_shared_final_report_constant() -> None:
    assert load_agent_config("report_writer").output_file_pattern == common.FINAL_REPORT_PATH


def test_zero_arg_construction_succeeds(patched_llm_factory, patched_settings) -> None:
    assert ReportWriterNode().name == "report_writer"


def test_resolve_node_returns_the_report_writer_node(patched_llm_factory, patched_settings) -> None:
    assert isinstance(resolve_node("report_writer"), ReportWriterNode)


def test_prompt_states_no_leaderboard_score_is_available() -> None:
    prompt = PromptLoader().load("report_writer", "v1")

    assert "no submission has been made and no\nleaderboard score exists" in prompt
    assert "runs **after you**" in prompt


# -- writing --------------------------------------------------------------


def test_call_writes_the_final_report_to_the_fixed_path(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    ReportWriterNode()(_build_state())

    assert [path for path, _ in mock_workspace_manager.written] == [REPORT_PATH]


def test_report_contains_the_llm_narrative_and_an_inputs_block(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    ReportWriterNode()(_build_state())

    _, content = mock_workspace_manager.written[0]
    assert "Gradient boosting over five frozen folds." in content
    assert "## Inputs" in content
    assert f"- `{CODE_REVIEW_PATH}` — read" in content


def test_delta_carries_only_messages(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    delta = ReportWriterNode()(_build_state())

    assert set(delta) == {"messages"}


# -- input resolution -----------------------------------------------------


def test_reads_previous_iteration_artifacts(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    ReportWriterNode()(_build_state(current_iteration=3))

    assert "reports/score_evaluation_2.json" in mock_workspace_manager.read_json_paths
    assert "reports/error_diagnosis_2.json" in mock_workspace_manager.read_json_paths
    assert "reports/hypotheses_2.json" in mock_workspace_manager.read_json_paths
    assert "reports/score_evaluation_3.json" not in mock_workspace_manager.read_json_paths


def test_reads_the_code_review_reviewer_just_wrote(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    ReportWriterNode()(_build_state())

    assert CODE_REVIEW_PATH in mock_workspace_manager.read_text_paths


def test_problem_definition_is_read_from_state_path_when_set(
    patched_llm_factory, patched_settings, json_artifacts, mock_workspace_manager
) -> None:
    json_artifacts["reports/custom_definition.json"] = _PROBLEM_DEFINITION

    ReportWriterNode()(_build_state(problem_definition_path="reports/custom_definition.json"))

    assert "reports/custom_definition.json" in mock_workspace_manager.read_json_paths
    assert "reports/problem_definition.json" not in mock_workspace_manager.read_json_paths


def test_problem_definition_falls_back_to_the_well_known_path(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    ReportWriterNode()(_build_state(problem_definition_path=""))

    assert "reports/problem_definition.json" in mock_workspace_manager.read_json_paths


# -- degradation ----------------------------------------------------------


def test_all_inputs_missing_still_writes_a_report(
    patched_llm_factory, patched_settings, json_artifacts, texts, mock_workspace_manager, mock_llm
) -> None:
    json_artifacts.clear()
    texts.clear()

    ReportWriterNode()(_build_state())

    assert len(mock_workspace_manager.written) == 1
    message = _injected_message(mock_llm)
    for placeholder in (
        "(problem definition not available)",
        "(score evaluation not available)",
        "(error diagnosis not available)",
        "(hypotheses not available)",
        "(code review not available)",
    ):
        assert placeholder in message


def test_unreadable_json_degrades_to_a_placeholder(
    patched_llm_factory, patched_settings, json_artifacts, mock_workspace_manager, mock_llm
) -> None:
    json_artifacts["reports/error_diagnosis_2.json"] = ValueError("truncated JSON")

    ReportWriterNode()(_build_state())

    message = _injected_message(mock_llm)
    assert "(error diagnosis not available)" in message
    assert "overfitting" not in message
    assert "roc_auc" in message


# -- run summary ----------------------------------------------------------


def test_run_summary_reports_state_scores(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    ReportWriterNode()(
        _build_state(
            baseline_score=0.78,
            best_score=0.85,
            iterations_without_improvement=4,
        )
    )

    message = _injected_message(mock_llm)
    assert "- baseline_score: 0.78" in message
    assert "- best_score: 0.85" in message
    assert "- iterations_without_improvement: 4" in message


def test_infinite_best_score_renders_as_not_recorded(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """`new_state` seeds `best_score = -inf`; printing that into the run's
    human-facing deliverable is a defect."""
    ReportWriterNode()(_build_state())

    message = _injected_message(mock_llm)
    run_summary = message.split("## Run summary", 1)[1].split("## Problem definition", 1)[0]
    assert "- best_score: not recorded" in run_summary
    assert "inf" not in run_summary


def test_experiments_list_is_capped(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    experiments = [{"id": f"exp_{i}", "path": f"experiments/exp_{i}"} for i in range(50)]

    ReportWriterNode()(_build_state(experiments=experiments))

    message = _injected_message(mock_llm)
    run_summary = message.split("## Run summary", 1)[1].split("## Problem definition", 1)[0]
    assert "- experiments_recorded: 50" in run_summary
    assert run_summary.count('"id":') == 10
    assert "40 further entries omitted" in run_summary


# -- budget ---------------------------------------------------------------


def test_injected_text_is_capped(
    patched_llm_factory, patched_settings, texts, mock_workspace_manager, mock_llm
) -> None:
    texts[CODE_REVIEW_PATH] = "r" * 60_000

    ReportWriterNode()(_build_state())

    message = _injected_message(mock_llm)
    assert f"characters of {CODE_REVIEW_PATH}" in message
    assert len(message) < common.MAX_INJECTED_CHARS + 5_000


# -- defensive helpers ----------------------------------------------------


@pytest.mark.parametrize("value", [None, "0.5", True, float("nan"), float("inf"), []])
def test_render_float_degrades_to_not_recorded(value: Any) -> None:
    assert report_writer_module._render_float(value) == "not recorded"


@pytest.mark.parametrize("value", [None, "3", 3.0, True, []])
def test_render_int_degrades_to_not_recorded(value: Any) -> None:
    assert report_writer_module._render_int(value) == "not recorded"


def test_unrenderable_experiment_entry_degrades() -> None:
    class _Hostile:
        def __str__(self) -> str:
            raise ValueError("cannot stringify")

    assert (
        report_writer_module._render_experiment_entry({"id": _Hostile()})
        == "(unrenderable experiment entry)"
    )


def test_budget_returns_the_exhausted_marker_once_spent() -> None:
    budget = report_writer_module._Budget(total=4)

    assert budget.spend("abcd", "src/x.py") == "abcd"
    assert budget.spend("more", "src/y.py") == common.BUDGET_EXHAUSTED
