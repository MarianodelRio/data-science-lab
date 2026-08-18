"""Unit tests for `src/nodes/compute/score_evaluator.py`.

Follows `tests/unit/nodes/compute/test_specialist_selector.py`/
`test_baseline_runner.py`'s conventions: a real `WorkspaceManager` over
`tmp_path`, pre-seeded fixture JSON files, and an AST-based static check for
the "no LLM import" invariant.

The "Boundaries" section below is the mutation-testing target for this
module (`devteam.config.yml` `critical_modules`): each test asserts the
*exact* value a mutant would corrupt, not merely a truthy/falsy shape.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

import src.nodes.compute.score_evaluator as score_evaluator_module
from src.nodes.compute.score_evaluator import ScoreEvaluatorNode
from src.state import new_state
from src.workspace.workspace_manager import WorkspaceManager

PROBLEM_DEFINITION_PATH = "reports/problem_definition.json"


def _seed_results(workspace: WorkspaceManager, directory: str, **fields: Any) -> None:
    payload: dict[str, Any] = {"cv_score": 0.5}
    payload.update(fields)
    workspace.write_json(f"{directory}/results.json", payload)


def _seed_problem_definition(workspace: WorkspaceManager, success_metric: str | None) -> None:
    payload: dict[str, Any] = {}
    if success_metric is not None:
        payload["success_metric"] = success_metric
    workspace.write_json(PROBLEM_DEFINITION_PATH, payload)


def _build_state(tmp_path, **overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = dict(new_state("comp", str(tmp_path)))
    state["problem_definition_path"] = PROBLEM_DEFINITION_PATH
    state.update(overrides)
    return state


def _read_artifact(tmp_path, iteration: int) -> dict[str, Any]:
    workspace = WorkspaceManager(str(tmp_path))
    return workspace.read_json(f"reports/score_evaluation_{iteration}.json")


# -- happy paths --


def test_maximize_metric_improvement_updates_best_score_and_path(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.9)
    state = _build_state(tmp_path, best_score=0.5, iterations_without_improvement=3)

    delta = ScoreEvaluatorNode().run(state)

    assert delta["last_score"] == pytest.approx(0.9)
    assert delta["score_delta"] == pytest.approx(0.4)
    assert delta["best_score"] == pytest.approx(0.9)
    assert delta["best_experiment_path"] == "experiments/exp_0"
    assert delta["iterations_without_improvement"] == 0


def test_maximize_metric_no_improvement_leaves_best_score_and_path_unchanged(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.9)
    state = _build_state(tmp_path, best_score=0.95, iterations_without_improvement=2)

    delta = ScoreEvaluatorNode().run(state)

    assert delta["last_score"] == pytest.approx(0.9)
    assert "best_score" not in delta
    assert "best_experiment_path" not in delta
    assert delta["iterations_without_improvement"] == 3


def test_minimize_metric_negates_raw_score_into_last_score(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "rmse")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.3)
    state = _build_state(tmp_path)

    delta = ScoreEvaluatorNode().run(state)

    assert delta["last_score"] == pytest.approx(-0.3)


def test_minimize_metric_lower_raw_score_counts_as_improvement(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "rmse")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.3)
    state = _build_state(tmp_path, best_score=-0.5)

    delta = ScoreEvaluatorNode().run(state)

    assert delta["best_score"] == pytest.approx(-0.3)


def test_score_delta_equals_last_score_minus_prior_best_score(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.5)
    state = _build_state(tmp_path, best_score=0.2)

    delta = ScoreEvaluatorNode().run(state)

    assert delta["score_delta"] == pytest.approx(0.3)


def test_experiment_dir_pointer_from_state_is_used_and_recorded_in_artifact(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_7", cv_score=0.9)
    state = _build_state(
        tmp_path,
        current_iteration=9,
        experiments=[{"path": "experiments/exp_7"}],
    )

    delta = ScoreEvaluatorNode().run(state)

    assert delta["best_experiment_path"] == "experiments/exp_7"
    artifact = _read_artifact(tmp_path, iteration=9)
    assert artifact["experiment_dir"] == "experiments/exp_7"


def test_baseline_comparable_metric_computes_delta_vs_baseline(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.9, metric="accuracy")
    state = _build_state(tmp_path, baseline_score=0.8)

    ScoreEvaluatorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["baseline_comparison_made"] is True
    assert artifact["delta_vs_baseline"] == pytest.approx(0.1)


def test_baseline_incomparable_metric_omits_delta_vs_baseline_with_reason(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.9, metric="rmse")
    state = _build_state(tmp_path, baseline_score=0.8)

    ScoreEvaluatorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["baseline_comparison_made"] is False
    assert artifact["delta_vs_baseline"] is None
    assert artifact["baseline_comparison_reason"]


def test_no_baseline_score_yet_omits_delta_vs_baseline_with_reason(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.9, metric="accuracy")
    state = _build_state(tmp_path)
    del state["baseline_score"]

    ScoreEvaluatorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["baseline_comparison_made"] is False
    assert artifact["delta_vs_baseline"] is None
    assert artifact["baseline_comparison_reason"]


def test_artifact_filename_uses_experiment_entrys_own_iteration_key(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_5", cv_score=0.5)
    state = _build_state(
        tmp_path,
        current_iteration=0,
        experiments=[{"path": "experiments/exp_5", "iteration": 5}],
    )

    ScoreEvaluatorNode().run(state)

    workspace_check = WorkspaceManager(str(tmp_path))
    assert workspace_check.read_json("reports/score_evaluation_5.json")


def test_artifact_filename_falls_back_to_current_iteration_when_entry_has_no_iteration_key(
    tmp_path,
) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_2", cv_score=0.5)
    state = _build_state(
        tmp_path,
        current_iteration=2,
        experiments=[{"path": "experiments/exp_2"}],
    )

    ScoreEvaluatorNode().run(state)

    workspace_check = WorkspaceManager(str(tmp_path))
    assert workspace_check.read_json("reports/score_evaluation_2.json")


# -- boundaries / mutation-killers --


def test_tie_score_is_not_an_improvement(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.7)
    state = _build_state(tmp_path, best_score=0.7, iterations_without_improvement=2)

    delta = ScoreEvaluatorNode().run(state)

    assert "best_score" not in delta
    assert "best_experiment_path" not in delta
    assert delta["iterations_without_improvement"] == 3


def test_first_evaluation_against_negative_infinity_best_score_has_zero_delta(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.6)
    state = _build_state(tmp_path)  # new_state() default: best_score == -inf

    delta = ScoreEvaluatorNode().run(state)

    assert delta["score_delta"] == 0.0
    assert delta["best_score"] == pytest.approx(0.6)


def test_score_delta_nonzero_when_prior_best_score_is_already_finite(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.6)
    state = _build_state(tmp_path, best_score=0.1)

    delta = ScoreEvaluatorNode().run(state)

    assert delta["score_delta"] == pytest.approx(0.5)
    assert delta["score_delta"] != 0.0


def test_same_raw_scores_flip_improvement_outcome_under_opposite_metric_direction(
    tmp_path,
) -> None:
    maximize_dir = tmp_path / "maximize"
    minimize_dir = tmp_path / "minimize"

    max_workspace = WorkspaceManager(str(maximize_dir))
    _seed_problem_definition(max_workspace, "accuracy")
    _seed_results(max_workspace, "experiments/exp_0", cv_score=0.5)
    max_state = _build_state(maximize_dir, best_score=0.0)
    max_delta = ScoreEvaluatorNode().run(max_state)

    min_workspace = WorkspaceManager(str(minimize_dir))
    _seed_problem_definition(min_workspace, "rmse")
    _seed_results(min_workspace, "experiments/exp_0", cv_score=0.5)
    min_state = _build_state(minimize_dir, best_score=0.0)
    min_delta = ScoreEvaluatorNode().run(min_state)

    assert "best_score" in max_delta
    assert "best_score" not in min_delta


def test_iterations_without_improvement_increments_from_nonzero_starting_value(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.1)
    state = _build_state(tmp_path, best_score=0.9, iterations_without_improvement=5)

    delta = ScoreEvaluatorNode().run(state)

    assert delta["iterations_without_improvement"] == 6


def test_iterations_without_improvement_resets_to_zero_on_improvement(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.9)
    state = _build_state(tmp_path, best_score=0.1, iterations_without_improvement=5)

    delta = ScoreEvaluatorNode().run(state)

    assert delta["iterations_without_improvement"] == 0


def test_iterations_without_improvement_increments_when_no_score_is_obtainable(tmp_path) -> None:
    state = _build_state(tmp_path, iterations_without_improvement=5)

    delta = ScoreEvaluatorNode().run(state)

    assert delta == {"iterations_without_improvement": 6}
    assert "last_score" not in delta
    assert "score_delta" not in delta


def test_non_finite_raw_score_is_treated_as_unavailable(tmp_path) -> None:
    results_path = tmp_path / "experiments" / "exp_0"
    results_path.mkdir(parents=True, exist_ok=True)
    (results_path / "results.json").write_text('{"cv_score": 1e400}', encoding="utf-8")
    state = _build_state(tmp_path, iterations_without_improvement=5)

    delta = ScoreEvaluatorNode().run(state)

    assert delta == {"iterations_without_improvement": 6}


def test_never_writes_infinite_or_nan_best_score_before_into_artifact(tmp_path) -> None:
    state = _build_state(tmp_path)  # bare: best_score == -inf

    ScoreEvaluatorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["best_score_before"] is None

    raw_text = (tmp_path / "reports" / "score_evaluation_0.json").read_text(encoding="utf-8")
    assert "Infinity" not in raw_text
    assert "NaN" not in raw_text


@pytest.mark.parametrize(
    "raw_metric",
    ["log_loss", "logloss", "Log Loss", "Log-Loss", "RMSE", " rmse "],
)
def test_metric_name_separator_variants_all_resolve_to_minimize(tmp_path, raw_metric) -> None:
    metric_dir = tmp_path / raw_metric.strip().replace(" ", "_").replace("-", "_")
    workspace = WorkspaceManager(str(metric_dir))
    _seed_problem_definition(workspace, raw_metric)
    _seed_results(workspace, "experiments/exp_0", cv_score=0.4)
    state = _build_state(metric_dir)

    ScoreEvaluatorNode().run(state)

    artifact = _read_artifact(metric_dir, iteration=0)
    assert artifact["direction"] == "minimize"


# -- degradation: none may raise --


def test_missing_results_json_returns_counter_only_delta(tmp_path) -> None:
    state = _build_state(tmp_path)

    delta = ScoreEvaluatorNode().run(state)

    assert delta == {"iterations_without_improvement": 1}


def test_malformed_results_json_degrades_safely(tmp_path) -> None:
    results_path = tmp_path / "experiments" / "exp_0"
    results_path.mkdir(parents=True, exist_ok=True)
    (results_path / "results.json").write_text("{not valid json", encoding="utf-8")
    state = _build_state(tmp_path)

    delta = ScoreEvaluatorNode().run(state)

    assert delta == {"iterations_without_improvement": 1}


def test_non_dict_results_json_degrades_safely(tmp_path) -> None:
    results_path = tmp_path / "experiments" / "exp_0"
    results_path.mkdir(parents=True, exist_ok=True)
    (results_path / "results.json").write_text("[1, 2, 3]", encoding="utf-8")
    state = _build_state(tmp_path)

    delta = ScoreEvaluatorNode().run(state)

    assert delta == {"iterations_without_improvement": 1}


def test_missing_cv_score_key_degrades_safely(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    workspace.write_json("experiments/exp_0/results.json", {"other": 1})
    state = _build_state(tmp_path)

    delta = ScoreEvaluatorNode().run(state)

    assert delta == {"iterations_without_improvement": 1}


def test_non_numeric_cv_score_degrades_safely(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    workspace.write_json("experiments/exp_0/results.json", {"cv_score": "high"})
    state = _build_state(tmp_path)

    delta = ScoreEvaluatorNode().run(state)

    assert delta == {"iterations_without_improvement": 1}


def test_missing_problem_definition_defaults_to_maximize_direction(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_results(workspace, "experiments/exp_0", cv_score=0.5)
    state = _build_state(tmp_path, problem_definition_path="")

    ScoreEvaluatorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["direction"] == "maximize"


def test_malformed_problem_definition_defaults_to_maximize_direction(tmp_path) -> None:
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "problem_definition.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    workspace = WorkspaceManager(str(tmp_path))
    _seed_results(workspace, "experiments/exp_0", cv_score=0.5)
    state = _build_state(tmp_path)

    ScoreEvaluatorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["direction"] == "maximize"


def test_empty_experiments_list_falls_back_to_well_known_experiment_dir(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.7)
    state = _build_state(tmp_path, experiments=[])

    delta = ScoreEvaluatorNode().run(state)

    assert delta["last_score"] == pytest.approx(0.7)


def test_absolute_experiment_path_outside_workspace_falls_back_to_well_known_dir(
    tmp_path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    workspace = WorkspaceManager(str(workspace_dir))
    _seed_problem_definition(workspace, "accuracy")
    _seed_results(workspace, "experiments/exp_0", cv_score=0.6)
    outside = str(tmp_path / "elsewhere" / "exp_9")
    state = _build_state(workspace_dir, experiments=[{"path": outside}])

    delta = ScoreEvaluatorNode().run(state)

    assert delta["last_score"] == pytest.approx(0.6)


def test_run_on_completely_bare_new_state_does_not_raise(tmp_path) -> None:
    state = dict(new_state("comp", str(tmp_path)))

    delta = ScoreEvaluatorNode().run(state)

    assert delta == {"iterations_without_improvement": 1}
    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["evaluated"] is False
    assert artifact["reason"]


# -- no-LLM-import invariant: AST-based static check --


def test_score_evaluator_module_does_not_import_llm_or_langchain() -> None:
    source_path = inspect.getfile(score_evaluator_module)
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
