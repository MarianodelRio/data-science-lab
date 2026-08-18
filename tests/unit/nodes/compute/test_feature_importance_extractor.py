"""Unit tests for `src/nodes/compute/feature_importance_extractor.py`.

Follows the same conventions as `test_score_evaluator.py`: a real
`WorkspaceManager` over `tmp_path`, pre-seeded fixture JSON files, and an
AST-based static check for the "no LLM import" invariant.
"""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest

import src.nodes.compute.feature_importance_extractor as fi_extractor_module
from src.nodes.compute.feature_importance_extractor import FeatureImportanceExtractorNode
from src.state import new_state
from src.workspace.workspace_manager import WorkspaceManager


def _seed_design(workspace: WorkspaceManager, directory: str, model_family: str) -> None:
    workspace.write_json(f"{directory}/design.json", {"model_family": model_family})


def _seed_results(workspace: WorkspaceManager, directory: str, **fields: Any) -> None:
    workspace.write_json(f"{directory}/results.json", fields)


def _build_state(tmp_path, **overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = dict(new_state("comp", str(tmp_path)))
    state.update(overrides)
    return state


def _read_artifact(tmp_path, iteration: int) -> dict[str, Any]:
    workspace = WorkspaceManager(str(tmp_path))
    return workspace.read_json(f"reports/feature_importance_{iteration}.json")


# -- happy path: tree model with a valid payload --


def test_tree_model_family_with_valid_payload_writes_ranked_json(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(
        workspace,
        "experiments/exp_0",
        feature_importance={"age": 0.3, "fare": 0.7},
    )
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is False
    assert artifact["model_family"] == "xgboost"
    assert artifact["experiment_dir"] == "experiments/exp_0"
    features = {f["feature"]: f for f in artifact["features"]}
    assert set(features) == {"age", "fare"}


@pytest.mark.parametrize(
    "model_family", ["xgboost", "lightgbm", "catboost", "extra_trees", "gradient_boosting_lags"]
)
def test_each_allowed_tree_family_is_accepted(tmp_path, model_family) -> None:
    family_dir = tmp_path / model_family
    workspace = WorkspaceManager(str(family_dir))
    _seed_design(workspace, "experiments/exp_0", model_family)
    _seed_results(workspace, "experiments/exp_0", feature_importance={"x": 1.0})
    state = _build_state(family_dir)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(family_dir, iteration=0)
    assert artifact["skipped"] is False
    assert artifact["model_family"] == model_family


@pytest.mark.parametrize("model_family", ["tabnet", "node", "mlp"])
def test_neural_model_family_skips_without_writing_ranked_data(tmp_path, model_family) -> None:
    family_dir = tmp_path / model_family
    workspace = WorkspaceManager(str(family_dir))
    _seed_design(workspace, "experiments/exp_0", model_family)
    _seed_results(workspace, "experiments/exp_0", feature_importance={"x": 1.0})
    state = _build_state(family_dir)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(family_dir, iteration=0)
    assert artifact["skipped"] is True
    assert artifact["features"] == []


def test_unknown_future_model_family_skips_safely(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "some_future_model_family")
    _seed_results(workspace, "experiments/exp_0", feature_importance={"x": 1.0})
    state = _build_state(tmp_path)

    delta = FeatureImportanceExtractorNode().run(state)

    assert delta == {}
    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is True
    assert "not in the tree allow-list" in artifact["reason"]


def test_missing_design_json_skips_with_reason(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_results(workspace, "experiments/exp_0", feature_importance={"x": 1.0})
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is True
    assert artifact["model_family"] is None
    assert "model_family" in artifact["reason"]


def test_missing_feature_importance_payload_skips_even_for_tree_family(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(workspace, "experiments/exp_0")
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is True
    assert "feature_importance" in artifact["reason"]


def test_malformed_feature_importance_payload_skips(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(workspace, "experiments/exp_0", feature_importance=[1, 2, 3])
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is True


def test_non_numeric_importance_values_are_dropped_not_fatal(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(
        workspace,
        "experiments/exp_0",
        feature_importance={"age": 0.5, "name": "not-a-number", "cabin": True},
    )
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is False
    features = {f["feature"] for f in artifact["features"]}
    assert features == {"age"}


def test_empty_string_key_in_importance_payload_is_dropped_not_fatal(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(
        workspace,
        "experiments/exp_0",
        feature_importance={"": 0.9, "age": 0.5},
    )
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is False
    features = {f["feature"] for f in artifact["features"]}
    assert features == {"age"}


def test_non_finite_importance_value_is_dropped_not_fatal(tmp_path) -> None:
    results_path = tmp_path / "experiments" / "exp_0"
    results_path.mkdir(parents=True, exist_ok=True)
    (results_path / "results.json").write_text(
        '{"feature_importance": {"age": 1e400, "fare": 0.3}}', encoding="utf-8"
    )
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is False
    features = {f["feature"] for f in artifact["features"]}
    assert features == {"fare"}


def test_all_non_numeric_importance_values_skips(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(
        workspace,
        "experiments/exp_0",
        feature_importance={"name": "not-a-number", "flag": True},
    )
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is True


def test_feature_names_filter_restricts_ranked_output(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(
        workspace,
        "experiments/exp_0",
        feature_importance={"age": 0.5, "fare": 0.3, "unexpected": 0.9},
        feature_names=["age", "fare"],
    )
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    features = {f["feature"] for f in artifact["features"]}
    assert features == {"age", "fare"}


def test_negative_importance_values_are_ranked_by_absolute_value(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(
        workspace,
        "experiments/exp_0",
        feature_importance={"age": -0.9, "fare": 0.1},
    )
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    ranked = sorted(artifact["features"], key=lambda f: f["rank"])
    assert ranked[0]["feature"] == "age"
    assert ranked[0]["importance"] == pytest.approx(0.9)


def test_normalized_importance_sums_to_one(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(
        workspace,
        "experiments/exp_0",
        feature_importance={"age": 0.2, "fare": 0.3, "sex": 0.5},
    )
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    total = sum(f["normalized_importance"] for f in artifact["features"])
    assert total == pytest.approx(1.0)


def test_rank_field_is_one_indexed_and_descending(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(
        workspace,
        "experiments/exp_0",
        feature_importance={"age": 0.1, "fare": 0.5, "sex": 0.3},
    )
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    ranked = sorted(artifact["features"], key=lambda f: f["rank"])
    assert [f["rank"] for f in ranked] == [1, 2, 3]
    assert [f["feature"] for f in ranked] == ["fare", "sex", "age"]


def test_run_returns_empty_dict_on_success_path(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(workspace, "experiments/exp_0", feature_importance={"age": 0.5})
    state = _build_state(tmp_path)

    delta = FeatureImportanceExtractorNode().run(state)

    assert delta == {}


def test_run_returns_empty_dict_on_skip_path(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "mlp")
    _seed_results(workspace, "experiments/exp_0", feature_importance={"age": 0.5})
    state = _build_state(tmp_path)

    delta = FeatureImportanceExtractorNode().run(state)

    assert delta == {}


def test_run_on_completely_bare_new_state_does_not_raise(tmp_path) -> None:
    state = dict(new_state("comp", str(tmp_path)))

    delta = FeatureImportanceExtractorNode().run(state)

    assert delta == {}
    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is True


# -- non-finite importance-total overflow (security finding: two
# extreme-magnitude entries are enough to overflow `sum()`, silently zeroing
# every normalized_importance rather than just the overflowing ones) --


def test_extreme_magnitude_importances_overflow_total_but_stay_finite_and_flagged(
    tmp_path,
) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(
        workspace,
        "experiments/exp_0",
        feature_importance={"f1": 1.5e308, "f2": 1.5e308, "f3": 0.0001},
    )
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["importance_total_overflowed"] is True
    # Ranking by raw magnitude stays correct even though the total overflowed.
    ranked = sorted(artifact["features"], key=lambda f: f["rank"])
    assert [f["feature"] for f in ranked[:2]] == ["f1", "f2"] or [
        f["feature"] for f in ranked[:2]
    ] == ["f2", "f1"]
    assert all(f["normalized_importance"] == 0.0 for f in artifact["features"])
    raw_text = (tmp_path / "reports" / "feature_importance_0.json").read_text(encoding="utf-8")
    assert "Infinity" not in raw_text


def test_experiment_resolution_warning_flags_stale_fallback_directory(tmp_path) -> None:
    """Same divergence `score_evaluator` guards against: an `experiments`
    entry naming a valid `iteration` whose `path` is unusable, so results
    (and here, `design.json`) are read from the well-known fallback
    directory instead -- which may describe a different experiment."""
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(workspace, "experiments/exp_0", feature_importance={"age": 0.5})
    state = _build_state(
        tmp_path,
        current_iteration=0,
        experiments=[{"iteration": 3}],  # no "path" key
    )

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    warning = artifact["experiment_resolution_warning"]
    assert warning is not None
    assert "3" in warning
    assert "0" in warning


def test_normal_magnitude_importances_do_not_flag_overflow(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(workspace, "experiments/exp_0", feature_importance={"age": 0.5, "fare": 0.5})
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["importance_total_overflowed"] is False


# -- payload-size cap (security finding: unbounded entry count from
# LLM/generated-script output flows uncapped into several full-size copies) --


def test_feature_importance_payload_beyond_cap_is_truncated_with_marker(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    oversized_payload = {f"feature_{i}": float(i) for i in range(3100)}
    _seed_results(workspace, "experiments/exp_0", feature_importance=oversized_payload)
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["skipped"] is False
    assert artifact["original_feature_count"] == 3100
    assert len(artifact["features"]) == 3000
    assert artifact["features_truncated"] is True
    # The kept entries are the largest-magnitude ones, not an arbitrary subset.
    kept = {f["feature"] for f in artifact["features"]}
    assert "feature_3099" in kept
    assert "feature_0" not in kept


def test_feature_importance_payload_within_cap_is_not_marked_truncated(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    _seed_design(workspace, "experiments/exp_0", "xgboost")
    _seed_results(workspace, "experiments/exp_0", feature_importance={"age": 0.5, "fare": 0.3})
    state = _build_state(tmp_path)

    FeatureImportanceExtractorNode().run(state)

    artifact = _read_artifact(tmp_path, iteration=0)
    assert artifact["features_truncated"] is False
    assert artifact["original_feature_count"] == 2


# -- no-LLM-import invariant: AST-based static check --


def test_feature_importance_extractor_module_does_not_import_llm_or_langchain() -> None:
    source_path = inspect.getfile(fi_extractor_module)
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


def test_no_shap_import_anywhere() -> None:
    """T-031's central design choice: this node extracts a pre-computed
    payload and never imports `shap`, avoiding both the declared-but-possibly-
    absent dependency risk and `node_resolver`'s hard failure on a broken
    transitive import in a landed node module."""
    source_path = inspect.getfile(fi_extractor_module)
    with open(source_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=source_path)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    assert not any(name == "shap" or name.startswith("shap.") for name in imported_names)
