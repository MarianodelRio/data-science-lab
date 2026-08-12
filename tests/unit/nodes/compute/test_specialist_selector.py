"""Unit tests for `src/nodes/compute/specialist_selector.py`.

Follows `tests/unit/nodes/compute/test_base.py`/`test_baseline_runner.py`'s conventions: a real
`WorkspaceManager` over `tmp_path`, pre-seeded fixture JSON files, `resolve_node` mocked at its
import location inside `src.nodes.compute.specialist_selector` for dispatch-shape assertions, and
an AST-based static check (mirroring `test_base.py`'s own) for the "no LLM import" invariant.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any
from unittest.mock import MagicMock, patch

import src.nodes.compute.specialist_selector as specialist_selector_module
from src.graph.nodes_noop import NoOpNode
from src.nodes.compute.specialist_selector import SpecialistSelectorNode
from src.state import new_state
from src.workspace.workspace_manager import WorkspaceManager

PROBLEM_DEFINITION_PATH = "reports/problem_definition.json"
SOLUTION_PLAN_PATH = "solution/solution_plan.json"


def _seed_workspace(
    tmp_path,
    problem_definition: dict[str, Any] | None = None,
    solution_plan: dict[str, Any] | None = None,
) -> WorkspaceManager:
    workspace = WorkspaceManager(str(tmp_path))
    if problem_definition is not None:
        workspace.write_json(PROBLEM_DEFINITION_PATH, problem_definition)
    if solution_plan is not None:
        workspace.write_json(SOLUTION_PLAN_PATH, solution_plan)
    return workspace


def _build_state(
    tmp_path,
    *,
    problem_definition_path: str = PROBLEM_DEFINITION_PATH,
    solution_plan_path: str = SOLUTION_PLAN_PATH,
    experiments: list[dict] | None = None,
) -> dict[str, Any]:
    state = new_state("comp", str(tmp_path))
    state["problem_definition_path"] = problem_definition_path
    state["solution_plan_path"] = solution_plan_path
    if experiments is not None:
        state["experiments"] = experiments
    return state


def _fake_experiments(n: int) -> list[dict]:
    return [
        {
            "id": f"exp-{i}",
            "path": f"experiments/exp-{i}",
            "cv_score": 0.5,
            "iteration": 0,
            "model": "m",
        }
        for i in range(n)
    ]


@patch("src.nodes.compute.specialist_selector.resolve_node")
def _run_and_get_selected(
    tmp_path, problem_definition, solution_plan, mock_resolve_node, experiments=None
):
    """Helper: seeds the workspace, mocks `resolve_node` to a no-op callable, runs the node,
    and returns the specialist name `resolve_node` was called with.

    `@patch` appends the mock as the next positional argument *after* whatever positional
    args the caller already supplies (not prepended) -- hence `mock_resolve_node` sits after
    the 3 caller-supplied positional params here, not first.
    """
    mock_target = MagicMock(return_value={})
    mock_resolve_node.return_value = mock_target
    _seed_workspace(tmp_path, problem_definition, solution_plan)
    state = _build_state(tmp_path, experiments=experiments)

    node = SpecialistSelectorNode()
    node.run(state)

    mock_resolve_node.assert_called_once()
    (called_name,), _ = mock_resolve_node.call_args
    return called_name


# -- done-when #1: tabular plan -> classical_ml_specialist --


def test_tabular_plan_selects_classical_ml_specialist(tmp_path) -> None:
    problem_definition = {"problem_type": "binary_classification"}
    solution_plan = {
        "model_families": ["lightgbm", "xgboost"],
        "order": ["lightgbm", "xgboost"],
        "ensembling_strategy": "",
        "rationale": "Tabular data with strong feature signal; gradient boosting is a strong fit.",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "classical_ml_specialist"


# -- done-when #2: plan with text features -> nlp_specialist --


def test_text_signal_in_rationale_selects_nlp_specialist(tmp_path) -> None:
    problem_definition = {"problem_type": "binary_classification"}
    solution_plan = {
        "model_families": ["logistic_regression"],
        "order": ["logistic_regression"],
        "ensembling_strategy": "",
        "rationale": "Use TF-IDF vectorization on the review text column.",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "nlp_specialist"


def test_bert_in_model_families_selects_nlp_specialist(tmp_path) -> None:
    problem_definition = {"problem_type": "binary_classification"}
    solution_plan = {
        "model_families": ["bert"],
        "order": ["bert"],
        "ensembling_strategy": "",
        "rationale": "",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "nlp_specialist"


# -- timeseries signal --


def test_time_series_problem_type_selects_timeseries_specialist(tmp_path) -> None:
    problem_definition = {"problem_type": "time_series_forecasting"}
    solution_plan = {
        "model_families": ["sarimax"],
        "order": ["sarimax"],
        "ensembling_strategy": "",
        "rationale": "",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "timeseries_specialist"


def test_prophet_in_model_families_selects_timeseries_specialist(tmp_path) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["prophet"],
        "order": ["prophet"],
        "ensembling_strategy": "",
        "rationale": "",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "timeseries_specialist"


def test_arima_in_order_selects_timeseries_specialist(tmp_path) -> None:
    problem_definition = {}
    solution_plan = {
        "model_families": ["statsmodels"],
        "order": ["arima"],
        "ensembling_strategy": "",
        "rationale": "",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "timeseries_specialist"


# -- deep learning signal, with no timeseries/NLP signal present --


def test_lstm_selects_deep_learning_specialist(tmp_path) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lstm"],
        "order": ["lstm"],
        "ensembling_strategy": "",
        "rationale": "",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "deep_learning_specialist"


def test_pytorch_neural_network_selects_deep_learning_specialist(tmp_path) -> None:
    problem_definition = {"problem_type": "multiclass_classification"}
    solution_plan = {
        "model_families": ["pytorch"],
        "order": ["pytorch"],
        "ensembling_strategy": "",
        "rationale": "A neural network implemented in PyTorch.",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "deep_learning_specialist"


# -- precedence: timeseries/NLP win over deep learning when both present --


def test_timeseries_signal_beats_deep_learning_signal(tmp_path) -> None:
    problem_definition = {"problem_type": "time_series_forecasting"}
    solution_plan = {
        "model_families": ["lstm"],
        "order": ["lstm"],
        "ensembling_strategy": "",
        "rationale": "An LSTM-based deep learning model for forecasting.",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "timeseries_specialist"


def test_nlp_signal_beats_deep_learning_signal(tmp_path) -> None:
    problem_definition = {"problem_type": "text_classification"}
    solution_plan = {
        "model_families": ["transformer"],
        "order": ["transformer"],
        "ensembling_strategy": "",
        "rationale": "A transformer-based deep learning model for text.",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "nlp_specialist"


# -- word-boundary false-positive avoidance --


def test_textile_does_not_falsely_match_text_keyword(tmp_path) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lightgbm"],
        "order": ["lightgbm"],
        "ensembling_strategy": "",
        "rationale": "Predicting textile manufacturing defect rates from sensor readings.",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "classical_ml_specialist"


def test_forecastable_does_not_falsely_match_forecast_keyword(tmp_path) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lightgbm"],
        "order": ["lightgbm"],
        "ensembling_strategy": "",
        "rationale": "This target variable is not easily forecastable using lag features alone.",
    }
    selected = _run_and_get_selected(tmp_path, problem_definition, solution_plan)
    assert selected == "classical_ml_specialist"


# -- done-when #3: ensemble never selected below 2 experiments (the critical guarantee) --


def test_one_experiment_with_ensembling_signal_does_not_select_ensemble(tmp_path) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lightgbm"],
        "order": ["lightgbm"],
        "ensembling_strategy": "Average predictions from all trained specialists via a weighted "
        "ensemble.",
        "rationale": "",
    }
    selected = _run_and_get_selected(
        tmp_path, problem_definition, solution_plan, experiments=_fake_experiments(1)
    )
    assert selected != "ensemble_specialist"
    assert selected == "classical_ml_specialist"


def test_zero_experiments_does_not_select_ensemble(tmp_path) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lightgbm"],
        "order": ["lightgbm"],
        "ensembling_strategy": "Ensemble everything.",
        "rationale": "",
    }
    selected = _run_and_get_selected(
        tmp_path, problem_definition, solution_plan, experiments=_fake_experiments(0)
    )
    assert selected != "ensemble_specialist"


# -- ensemble: 2+ experiments AND a real ensembling signal -> ensemble_specialist --


def test_two_experiments_with_ensembling_signal_selects_ensemble_specialist(tmp_path) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lightgbm"],
        "order": ["lightgbm"],
        "ensembling_strategy": "Stack the classical_ml and deep_learning results into a "
        "blended ensemble.",
        "rationale": "",
    }
    selected = _run_and_get_selected(
        tmp_path, problem_definition, solution_plan, experiments=_fake_experiments(2)
    )
    assert selected == "ensemble_specialist"


def test_more_than_two_experiments_with_ensembling_signal_selects_ensemble_specialist(
    tmp_path,
) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lightgbm"],
        "order": ["lightgbm"],
        "ensembling_strategy": "Blend all specialist outputs.",
        "rationale": "",
    }
    selected = _run_and_get_selected(
        tmp_path, problem_definition, solution_plan, experiments=_fake_experiments(3)
    )
    assert selected == "ensemble_specialist"


# -- ensemble: 2+ experiments but explicit "no ensembling" text -> not ensemble --


def test_no_ensembling_text_with_two_experiments_does_not_select_ensemble(tmp_path) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lightgbm"],
        "order": ["lightgbm"],
        "ensembling_strategy": "No ensembling this iteration; revisit after more experiments land.",
        "rationale": "",
    }
    selected = _run_and_get_selected(
        tmp_path, problem_definition, solution_plan, experiments=_fake_experiments(2)
    )
    assert selected != "ensemble_specialist"


def test_no_ensemble_singular_text_with_two_experiments_does_not_select_ensemble(tmp_path) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lightgbm"],
        "order": ["lightgbm"],
        "ensembling_strategy": "no ensemble planned yet",
        "rationale": "",
    }
    selected = _run_and_get_selected(
        tmp_path, problem_definition, solution_plan, experiments=_fake_experiments(2)
    )
    assert selected != "ensemble_specialist"


# -- ensemble: 2+ experiments but missing/empty ensembling_strategy -> not ensemble --


def test_missing_ensembling_strategy_with_two_experiments_does_not_select_ensemble(
    tmp_path,
) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lightgbm"],
        "order": ["lightgbm"],
        "rationale": "",
        # no "ensembling_strategy" key at all
    }
    selected = _run_and_get_selected(
        tmp_path, problem_definition, solution_plan, experiments=_fake_experiments(2)
    )
    assert selected != "ensemble_specialist"


def test_empty_ensembling_strategy_with_two_experiments_does_not_select_ensemble(tmp_path) -> None:
    problem_definition = {"problem_type": "regression"}
    solution_plan = {
        "model_families": ["lightgbm"],
        "order": ["lightgbm"],
        "ensembling_strategy": "",
        "rationale": "",
    }
    selected = _run_and_get_selected(
        tmp_path, problem_definition, solution_plan, experiments=_fake_experiments(2)
    )
    assert selected != "ensemble_specialist"


# -- ensemble override takes precedence over a 1-4 signal when both conditions are met --


def test_ensemble_override_beats_timeseries_signal(tmp_path) -> None:
    problem_definition = {"problem_type": "time_series_forecasting"}
    solution_plan = {
        "model_families": ["prophet"],
        "order": ["prophet"],
        "ensembling_strategy": "Ensemble the timeseries and classical results.",
        "rationale": "",
    }
    selected = _run_and_get_selected(
        tmp_path, problem_definition, solution_plan, experiments=_fake_experiments(2)
    )
    assert selected == "ensemble_specialist"


# -- graceful degradation --


def test_unset_paths_default_to_classical_ml(tmp_path) -> None:
    with patch("src.nodes.compute.specialist_selector.resolve_node") as mock_resolve_node:
        mock_resolve_node.return_value = MagicMock(return_value={})
        state = new_state("comp", str(tmp_path))
        state["problem_definition_path"] = ""
        state["solution_plan_path"] = ""

        node = SpecialistSelectorNode()
        node.run(state)

        (called_name,), _ = mock_resolve_node.call_args
        assert called_name == "classical_ml_specialist"


def test_missing_files_on_disk_default_to_classical_ml(tmp_path) -> None:
    with patch("src.nodes.compute.specialist_selector.resolve_node") as mock_resolve_node:
        mock_resolve_node.return_value = MagicMock(return_value={})
        # WorkspaceManager root exists (via new_state's workspace_path), but neither JSON file
        # was ever written.
        state = _build_state(tmp_path)

        node = SpecialistSelectorNode()
        node.run(state)

        (called_name,), _ = mock_resolve_node.call_args
        assert called_name == "classical_ml_specialist"


def test_malformed_non_dict_json_defaults_to_classical_ml(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    # Valid JSON, but a list rather than an object -- must degrade, not raise.
    workspace.write_text(PROBLEM_DEFINITION_PATH, "[1, 2, 3]")
    # Not valid JSON at all -- must also degrade, not raise.
    workspace.write_text(SOLUTION_PLAN_PATH, "not valid json at all")

    with patch("src.nodes.compute.specialist_selector.resolve_node") as mock_resolve_node:
        mock_resolve_node.return_value = MagicMock(return_value={})
        state = _build_state(tmp_path)

        node = SpecialistSelectorNode()
        node.run(state)

        (called_name,), _ = mock_resolve_node.call_args
        assert called_name == "classical_ml_specialist"


# -- absolute-path branch of `_to_relative`: the production-realistic case. In real pipeline
# execution `state["problem_definition_path"]`/`state["solution_plan_path"]` are always the
# *absolute* path `WorkspaceManager.write_json` returns (workspace_manager.py:76), never the
# already-relative strings every test above uses for brevity. --


def test_absolute_path_inside_workspace_reads_correctly_and_dispatches(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    absolute_problem_definition_path = workspace.write_json(
        PROBLEM_DEFINITION_PATH, {"problem_type": "time_series_forecasting"}
    )
    absolute_solution_plan_path = workspace.write_json(
        SOLUTION_PLAN_PATH,
        {
            "model_families": ["prophet"],
            "order": ["prophet"],
            "ensembling_strategy": "",
            "rationale": "",
        },
    )
    state = new_state("comp", str(tmp_path))
    state["problem_definition_path"] = absolute_problem_definition_path
    state["solution_plan_path"] = absolute_solution_plan_path

    with patch("src.nodes.compute.specialist_selector.resolve_node") as mock_resolve_node:
        mock_resolve_node.return_value = MagicMock(return_value={})

        node = SpecialistSelectorNode()
        node.run(state)

        mock_resolve_node.assert_called_once_with("timeseries_specialist")


def test_absolute_path_outside_workspace_degrades_to_classical_ml(tmp_path) -> None:
    outside_dir = tmp_path.parent / "outside_workspace_for_test"
    outside_dir.mkdir(exist_ok=True)
    outside_workspace = WorkspaceManager(str(outside_dir))
    # Written under a *different* workspace root than the one `state["workspace_path"]` points
    # at below -- `_to_relative`'s `relative_to` call raises `ValueError` for this path, which
    # `_read_json_dict` catches and degrades to `{}`, rather than crashing.
    outside_absolute_path = outside_workspace.write_json(
        PROBLEM_DEFINITION_PATH, {"problem_type": "time_series_forecasting"}
    )

    state = new_state("comp", str(tmp_path))
    state["problem_definition_path"] = outside_absolute_path
    state["solution_plan_path"] = ""

    with patch("src.nodes.compute.specialist_selector.resolve_node") as mock_resolve_node:
        mock_resolve_node.return_value = MagicMock(return_value={})

        node = SpecialistSelectorNode()
        node.run(state)

        # Degrades to {} rather than raising -- problem_definition never got read, so no
        # timeseries signal reaches the blob, and the classical_ml default applies.
        mock_resolve_node.assert_called_once_with("classical_ml_specialist")


# -- dispatch: resolve_node called with exactly the expected name and state; run() returns
# exactly what the mocked specialist callable returned --


def test_run_returns_exactly_the_dispatched_specialists_delta(tmp_path) -> None:
    expected_delta = {"messages": [], "some_field": "some_value"}
    with patch("src.nodes.compute.specialist_selector.resolve_node") as mock_resolve_node:
        mock_target = MagicMock(return_value=expected_delta)
        mock_resolve_node.return_value = mock_target
        _seed_workspace(
            tmp_path,
            {"problem_type": "binary_classification"},
            {
                "model_families": ["lightgbm"],
                "order": ["lightgbm"],
                "ensembling_strategy": "",
                "rationale": "",
            },
        )
        state = _build_state(tmp_path)

        node = SpecialistSelectorNode()
        delta = node.run(state)

        mock_resolve_node.assert_called_once_with("classical_ml_specialist")
        mock_target.assert_called_once_with(state)
        assert delta == expected_delta


# -- dispatch defensive guard: non-dict return value -> run() returns {} --


def test_non_dict_specialist_return_value_becomes_empty_dict(tmp_path) -> None:
    with patch("src.nodes.compute.specialist_selector.resolve_node") as mock_resolve_node:
        mock_resolve_node.return_value = MagicMock(return_value=None)
        _seed_workspace(
            tmp_path,
            {"problem_type": "binary_classification"},
            {
                "model_families": ["lightgbm"],
                "order": ["lightgbm"],
                "ensembling_strategy": "",
                "rationale": "",
            },
        )
        state = _build_state(tmp_path)

        node = SpecialistSelectorNode()
        delta = node.run(state)

        assert delta == {}


# -- real, un-mocked resolve_node: confirms the genuine NoOpNode fallback today --


def test_real_resolve_node_falls_back_to_noop_and_returns_empty_dict(tmp_path) -> None:
    _seed_workspace(
        tmp_path,
        {"problem_type": "binary_classification"},
        {
            "model_families": ["lightgbm"],
            "order": ["lightgbm"],
            "ensembling_strategy": "",
            "rationale": "",
        },
    )
    state = _build_state(tmp_path)

    node = SpecialistSelectorNode()
    delta = node.run(state)

    assert delta == {}


def test_real_resolve_node_resolves_classical_ml_specialist_to_noopnode(tmp_path) -> None:
    from src.graph.node_resolver import resolve_node

    _seed_workspace(tmp_path, {"problem_type": "binary_classification"}, {})
    resolved = resolve_node("classical_ml_specialist")
    assert isinstance(resolved, NoOpNode)
    assert resolved.name == "classical_ml_specialist"


# -- ComputeNode.__call__ delegates to run --


def test_call_delegates_to_run(tmp_path) -> None:
    with patch("src.nodes.compute.specialist_selector.resolve_node") as mock_resolve_node:
        expected_delta = {"messages": []}
        mock_resolve_node.return_value = MagicMock(return_value=expected_delta)
        _seed_workspace(
            tmp_path,
            {"problem_type": "binary_classification"},
            {
                "model_families": ["lightgbm"],
                "order": ["lightgbm"],
                "ensembling_strategy": "",
                "rationale": "",
            },
        )
        state = _build_state(tmp_path)

        node = SpecialistSelectorNode()
        delta = node(state)

        assert delta == expected_delta


# -- no-LLM-import invariant (done-when #4): AST-based static check --


def test_module_does_not_import_llm_or_langchain() -> None:
    source_path = inspect.getfile(specialist_selector_module)
    with open(source_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=source_path)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    forbidden_prefixes = ("src.llm", "src.nodes.llm", "langchain")
    offending = [
        name
        for name in imported_names
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
    ]
    assert offending == []
