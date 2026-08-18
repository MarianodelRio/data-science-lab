"""Unit tests for `src/nodes/compute/_evaluation_common.py` -- the shared
experiment-directory resolution and degrade-safe JSON reading used by both
`score_evaluator` and `feature_importance_extractor`.

Follows `tests/unit/nodes/compute/test_specialist_selector.py`'s convention:
a real `WorkspaceManager` over `tmp_path`, and an AST-based static check for
the "no LLM import" invariant.
"""

from __future__ import annotations

import ast
import inspect

import src.nodes.compute._evaluation_common as evaluation_common_module
from src.nodes.compute._evaluation_common import (
    DEGRADE_ERRORS,
    DESIGN_FILENAME,
    EXPERIMENT_DIR_PATTERN,
    RESULTS_FILENAME,
    candidate_experiment_dirs,
    experiment_dir_from_state,
    read_experiment_results,
    read_json_dict,
    relative_to_workspace,
    resolve_iteration,
)
from src.workspace.workspace_manager import WorkspaceManager


def test_module_constants_match_documented_shape() -> None:
    assert (OSError, ValueError, RecursionError) == DEGRADE_ERRORS
    assert EXPERIMENT_DIR_PATTERN == "experiments/exp_{iteration}"
    assert RESULTS_FILENAME == "results.json"
    assert DESIGN_FILENAME == "design.json"


# -- relative_to_workspace --


def test_relative_to_workspace_passes_through_already_relative_path(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    assert relative_to_workspace("experiments/exp_0/results.json", workspace) == (
        "experiments/exp_0/results.json"
    )


def test_relative_to_workspace_relativizes_absolute_path_inside_workspace(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    absolute = str(workspace.workspace_path / "reports" / "score_evaluation_0.json")
    assert relative_to_workspace(absolute, workspace) == "reports/score_evaluation_0.json"


def test_relative_to_workspace_raises_on_absolute_path_outside_workspace(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path / "workspace"))
    outside = str(tmp_path / "elsewhere" / "results.json")
    try:
        relative_to_workspace(outside, workspace)
    except ValueError:
        return
    raise AssertionError("expected ValueError for a path outside the workspace root")


# -- read_json_dict --


def test_read_json_dict_degrades_on_missing_file(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    assert read_json_dict("reports/does_not_exist.json", workspace) == {}


def test_read_json_dict_degrades_on_malformed_json(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert read_json_dict("broken.json", workspace) == {}


def test_read_json_dict_degrades_on_non_dict_json(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert read_json_dict("list.json", workspace) == {}


def test_read_json_dict_degrades_on_empty_path(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    assert read_json_dict("", workspace) == {}


def test_read_json_dict_reads_real_object(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    workspace.write_json("reports/results.json", {"cv_score": 0.9})
    assert read_json_dict("reports/results.json", workspace) == {"cv_score": 0.9}


# -- experiment_dir_from_state --


def test_experiment_dir_from_state_uses_last_experiment_path(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    state = {"experiments": [{"path": "experiments/exp_3"}]}
    assert experiment_dir_from_state(state, workspace) == "experiments/exp_3"


def test_experiment_dir_from_state_strips_file_suffix_to_parent(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    state = {"experiments": [{"path": "experiments/exp_3/results.json"}]}
    assert experiment_dir_from_state(state, workspace) == "experiments/exp_3"


def test_experiment_dir_from_state_returns_none_for_empty_experiments(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    assert experiment_dir_from_state({"experiments": []}, workspace) is None
    assert experiment_dir_from_state({}, workspace) is None


def test_experiment_dir_from_state_returns_none_for_traversal_path(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    state = {"experiments": [{"path": "experiments/../../etc/passwd"}]}
    assert experiment_dir_from_state(state, workspace) is None


def test_experiment_dir_from_state_returns_none_for_path_outside_workspace(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path / "workspace"))
    state = {"experiments": [{"path": str(tmp_path / "elsewhere" / "exp_0")}]}
    assert experiment_dir_from_state(state, workspace) is None


# -- candidate_experiment_dirs --


def test_candidate_experiment_dirs_always_includes_well_known_fallback(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    dirs = candidate_experiment_dirs({"current_iteration": 4}, workspace)
    assert "experiments/exp_4" in dirs
    assert len(dirs) >= 1


def test_candidate_experiment_dirs_dedupes_when_pointer_matches_fallback(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    state = {
        "current_iteration": 2,
        "experiments": [{"path": "experiments/exp_2"}],
    }
    dirs = candidate_experiment_dirs(state, workspace)
    assert dirs == ["experiments/exp_2"]


def test_candidate_experiment_dirs_lists_pointer_before_fallback(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    state = {
        "current_iteration": 9,
        "experiments": [{"path": "experiments/exp_1"}],
    }
    assert candidate_experiment_dirs(state, workspace) == [
        "experiments/exp_1",
        "experiments/exp_9",
    ]


# -- read_experiment_results --


def test_read_experiment_results_prefers_first_candidate_with_data(tmp_path) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    workspace.write_json("experiments/exp_1/results.json", {"cv_score": 0.5})
    workspace.write_json("experiments/exp_9/results.json", {"cv_score": 0.1})
    state = {
        "current_iteration": 9,
        "experiments": [{"path": "experiments/exp_1"}],
    }
    directory, results = read_experiment_results(state, workspace)
    assert directory == "experiments/exp_1"
    assert results == {"cv_score": 0.5}


def test_read_experiment_results_falls_back_to_well_known_dir_when_pointer_unusable(
    tmp_path,
) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    workspace.write_json("experiments/exp_0/results.json", {"cv_score": 0.7})
    state = {"current_iteration": 0, "experiments": []}
    directory, results = read_experiment_results(state, workspace)
    assert directory == "experiments/exp_0"
    assert results == {"cv_score": 0.7}


def test_read_experiment_results_returns_first_candidate_and_empty_dict_when_nothing_found(
    tmp_path,
) -> None:
    workspace = WorkspaceManager(str(tmp_path))
    state = {
        "current_iteration": 9,
        "experiments": [{"path": "experiments/exp_1"}],
    }
    directory, results = read_experiment_results(state, workspace)
    assert directory == "experiments/exp_1"
    assert results == {}


# -- resolve_iteration --


def test_resolve_iteration_prefers_experiment_entry_iteration_key(tmp_path) -> None:
    state = {"current_iteration": 9, "experiments": [{"iteration": 3}]}
    assert resolve_iteration(state) == 3


def test_resolve_iteration_falls_back_to_current_iteration_when_entry_key_absent(
    tmp_path,
) -> None:
    state = {"current_iteration": 5, "experiments": [{"path": "experiments/exp_5"}]}
    assert resolve_iteration(state) == 5


def test_resolve_iteration_falls_back_to_current_iteration_when_experiments_empty(
    tmp_path,
) -> None:
    assert resolve_iteration({"current_iteration": 2, "experiments": []}) == 2
    assert resolve_iteration({}) == 0


def test_resolve_iteration_ignores_bool_iteration_and_falls_back() -> None:
    state = {"current_iteration": 4, "experiments": [{"iteration": True}]}
    assert resolve_iteration(state) == 4


# -- no-LLM-import invariant: AST-based static check --


def test_evaluation_common_module_does_not_import_llm_or_langchain() -> None:
    source_path = inspect.getfile(evaluation_common_module)
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
