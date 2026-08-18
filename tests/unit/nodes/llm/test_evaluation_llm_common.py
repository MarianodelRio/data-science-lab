"""Unit tests for src/nodes/llm/_evaluation_llm_common.py — the JSON extraction,
degrade-safe artifact reading and validation helpers shared by the three
Pipeline Phase 6 LLM nodes.

No LLM, no network, no mocks: every function here is either pure data
transformation/validation or does real filesystem I/O against a
`tmp_path`-backed `WorkspaceManager` (the `test_experiment_design.py`
precedent).
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from src.nodes.llm import _evaluation_llm_common as common
from src.nodes.llm._evaluation_llm_common import (
    DEGRADE_ERRORS,
    ROOT_CAUSES,
    current_iteration,
    extract_json_object,
    join_experiment_file,
    read_workspace_json,
    render_json_section,
    validate_enum,
    validate_int,
    validate_non_empty_str,
    validate_object_list,
    validate_rank_permutation,
    validate_str_list,
    validate_unit_interval,
)
from src.state import LabState, new_state
from src.workspace.workspace_manager import WorkspaceManager

NODE = "error_analyst"
ARTIFACT = "reports/score_evaluation_0.json"
MISSING = "(score evaluation not yet available)"


def _workspace_state(tmp_path: Path) -> tuple[WorkspaceManager, LabState]:
    workspace = WorkspaceManager(str(tmp_path))
    state = new_state("comp", str(tmp_path))
    return workspace, state


def _deeply_nested_payload(depth: int = 100_000) -> dict[str, Any]:
    """A payload whose `json.dumps` exhausts the interpreter stack. Built
    iteratively so constructing it does not itself recurse."""
    nested: Any = []
    for _ in range(depth):
        nested = [nested]
    return {"nested": nested}


# -- the degrade set itself --


def test_degrade_errors_covers_oserror_valueerror_and_recursionerror() -> None:
    """Pinned as a set, not merely used: `RecursionError` is a `RuntimeError`, so
    a future edit narrowing this tuple back to `OSError` would silently
    reintroduce the abort-the-whole-graph behavior the readers exist to prevent."""
    assert (OSError, ValueError, RecursionError) == DEGRADE_ERRORS


def test_root_causes_vocabulary_is_the_five_design_tokens() -> None:
    assert ROOT_CAUSES == (
        "overfitting",
        "underfitting",
        "cv_lb_divergence",
        "feature_quality",
        "wrong_model_family",
    )


# -- module hygiene (adjustment 5) --


def test_module_declares_no_class_matching_its_own_stem() -> None:
    """`src/graph/node_resolver.py`'s `_find_node_class` picks a node class out of
    `src/nodes/llm/{name}.py` by its `name` attribute. A class here named after
    this module's stem would make it resolvable as a pipeline node."""
    matching = [
        obj
        for obj in vars(common).values()
        if inspect.isclass(obj) and getattr(obj, "name", None) == "_evaluation_llm_common"
    ]

    assert matching == []


# -- extract_json_object --


def test_extract_json_object_parses_unfenced_json() -> None:
    assert extract_json_object('{"root_cause": "overfitting"}', NODE) == {
        "root_cause": "overfitting"
    }


def test_extract_json_object_parses_json_labeled_fence() -> None:
    content = '```json\n{"root_cause": "underfitting"}\n```'

    assert extract_json_object(content, NODE) == {"root_cause": "underfitting"}


def test_extract_json_object_parses_unlabeled_fence() -> None:
    content = '```\n{"root_cause": "underfitting"}\n```'

    assert extract_json_object(content, NODE) == {"root_cause": "underfitting"}


def test_extract_json_object_salvages_preamble_prose() -> None:
    content = 'Here is my diagnosis:\n{"root_cause": "overfitting"}'

    assert extract_json_object(content, NODE) == {"root_cause": "overfitting"}


def test_extract_json_object_salvages_postamble_prose_after_a_closed_fence() -> None:
    content = '```json\n{"root_cause": "overfitting"}\n```\nHope that helps!'

    assert extract_json_object(content, NODE) == {"root_cause": "overfitting"}


def test_extract_json_object_salvages_unclosed_fence() -> None:
    content = '```json\n{"root_cause": "feature_quality"}'

    assert extract_json_object(content, NODE) == {"root_cause": "feature_quality"}


def test_extract_json_object_rejects_a_single_line_fence_with_no_content() -> None:
    """A fence opened and closed on one line has no newline to split on, so the
    fence handler itself fails before the salvage is attempted."""
    with pytest.raises(ValueError, match=NODE):
        extract_json_object("``````", NODE)


def test_extract_json_object_rejects_an_unclosed_fence_with_no_salvageable_braces() -> None:
    """The salvage window needs a `{`...`}` pair; without one the fence error is
    re-raised rather than swallowed."""
    with pytest.raises(ValueError, match="never closes it"):
        extract_json_object("```json\nno json here at all\n", NODE)


def test_extract_json_object_reraises_the_original_error_when_the_salvage_also_fails() -> None:
    """The salvage window is a brace-delimited slice; when that slice is itself
    unparseable the *original* parse error is what surfaces, not the salvage's."""
    with pytest.raises(ValueError, match="is not valid JSON"):
        extract_json_object('prose {"root_cause": } more prose', NODE)


def test_extract_json_object_rejects_unparseable_content_naming_the_node() -> None:
    with pytest.raises(ValueError, match=NODE):
        extract_json_object("not json at all", NODE)


def test_extract_json_object_rejects_a_top_level_array_naming_the_node() -> None:
    with pytest.raises(ValueError, match=NODE):
        extract_json_object('[{"root_cause": "overfitting"}]', NODE)


def test_extract_json_object_attributes_the_error_to_the_calling_node() -> None:
    with pytest.raises(ValueError, match="experiment_designer"):
        extract_json_object("not json at all", "experiment_designer")


# -- read_workspace_json: the degrade matrix (binding discovery 3) --


def test_read_workspace_json_returns_the_parsed_object(tmp_path: Path) -> None:
    workspace, _ = _workspace_state(tmp_path)
    workspace.write_json(ARTIFACT, {"iteration": 0, "experiment_dir": "experiments/exp_0"})

    assert read_workspace_json(ARTIFACT, workspace) == {
        "iteration": 0,
        "experiment_dir": "experiments/exp_0",
    }


def test_read_workspace_json_accepts_an_absolute_path_inside_the_workspace(
    tmp_path: Path,
) -> None:
    """`WorkspaceManager.write_json` returns an absolute path and nodes store it
    verbatim; `read_json` rejects absolute input, so the reader re-relativizes."""
    workspace, _ = _workspace_state(tmp_path)
    written = workspace.write_json(ARTIFACT, {"iteration": 0})

    assert read_workspace_json(written, workspace) == {"iteration": 0}


def test_read_workspace_json_returns_none_for_a_missing_file(tmp_path: Path) -> None:
    workspace, _ = _workspace_state(tmp_path)

    assert read_workspace_json(ARTIFACT, workspace) is None


@pytest.mark.parametrize("content", ['{"iteration": 0', "", "   \n"])
def test_read_workspace_json_returns_none_for_truncated_json(tmp_path: Path, content: str) -> None:
    workspace, _ = _workspace_state(tmp_path)
    workspace.write_text(ARTIFACT, content)

    assert read_workspace_json(ARTIFACT, workspace) is None


def test_read_workspace_json_returns_none_for_invalid_utf8(tmp_path: Path) -> None:
    workspace, _ = _workspace_state(tmp_path)
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "score_evaluation_0.json").write_bytes(b'{"a": "\xff\xfe"}')

    assert read_workspace_json(ARTIFACT, workspace) is None


def test_read_workspace_json_returns_none_for_a_path_outside_the_workspace(
    tmp_path: Path,
) -> None:
    """A resumed run can carry an absolute path recorded against a workspace root
    that has since moved — `relative_to_workspace` raises `ValueError` there."""
    workspace, _ = _workspace_state(tmp_path)

    assert read_workspace_json("/elsewhere/reports/score_evaluation_0.json", workspace) is None


def test_read_workspace_json_returns_none_for_a_traversal_path(tmp_path: Path) -> None:
    workspace, _ = _workspace_state(tmp_path)

    assert read_workspace_json("../../etc/passwd", workspace) is None


def test_read_workspace_json_returns_none_for_pathological_nesting(tmp_path: Path) -> None:
    """`RecursionError` is a `RuntimeError`, so neither `OSError` nor `ValueError`
    would catch it — but this reader promises never to raise."""
    workspace, _ = _workspace_state(tmp_path)
    depth = 100_000
    workspace.write_text(ARTIFACT, "[" * depth + "]" * depth)

    assert read_workspace_json(ARTIFACT, workspace) is None


@pytest.mark.parametrize("path", [None, "", "   ", 123, Path("reports/x.json")])
def test_read_workspace_json_returns_none_for_a_non_string_path(tmp_path: Path, path: Any) -> None:
    """`Path(42)` raises `TypeError`, which is not in the caught set — the
    `isinstance(path, str)` guard is what keeps that from escaping."""
    workspace, _ = _workspace_state(tmp_path)

    assert read_workspace_json(path, workspace) is None


def test_read_workspace_json_returns_none_when_the_payload_is_not_an_object(
    tmp_path: Path,
) -> None:
    workspace, _ = _workspace_state(tmp_path)
    workspace.write_text(ARTIFACT, json.dumps([1, 2, 3]))

    assert read_workspace_json(ARTIFACT, workspace) is None


# -- render_json_section --


def test_render_json_section_returns_the_missing_message_for_none() -> None:
    assert render_json_section(None, MISSING) == MISSING


def test_render_json_section_pretty_prints_a_dict() -> None:
    rendered = render_json_section({"root_cause": "overfitting"}, MISSING)

    assert json.loads(rendered) == {"root_cause": "overfitting"}
    assert "\n" in rendered  # indent=2, not a one-liner


def test_render_json_section_degrades_when_serialization_itself_recurses() -> None:
    """The serialization half of the degrade contract: a payload that was read
    successfully can still blow the stack inside `json.dumps` on the way back
    out into the prompt."""
    rendered = render_json_section(_deeply_nested_payload(), MISSING)

    assert rendered == "(unable to render this artifact as JSON)"


# -- current_iteration --


def test_current_iteration_defaults_to_zero_when_absent() -> None:
    assert current_iteration({}) == 0  # type: ignore[typeddict-item]


def test_current_iteration_returns_the_integer_value() -> None:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = 3

    assert current_iteration(state) == 3


def test_current_iteration_rejects_a_boolean() -> None:
    """`isinstance(True, int)` is `True`, so an unguarded read would interpolate
    `reports/error_diagnosis_True.json`."""
    state = new_state("comp", "/workspace")
    state["current_iteration"] = True  # type: ignore[typeddict-item]

    assert current_iteration(state) == 0


def test_current_iteration_rejects_a_numeric_string() -> None:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = "3"  # type: ignore[typeddict-item]

    assert current_iteration(state) == 0


# -- join_experiment_file --


def test_join_experiment_file_joins_a_relative_directory() -> None:
    assert join_experiment_file("experiments/exp_2", "results.json") == (
        "experiments/exp_2/results.json"
    )


def test_join_experiment_file_tolerates_a_trailing_slash() -> None:
    assert join_experiment_file("experiments/exp_2/", "design.json") == (
        "experiments/exp_2/design.json"
    )


@pytest.mark.parametrize("experiment_dir", [None, "", "   ", 7, ["experiments/exp_0"]])
def test_join_experiment_file_rejects_a_non_string_directory(experiment_dir: Any) -> None:
    assert join_experiment_file(experiment_dir, "results.json") is None


def test_join_experiment_file_rejects_a_traversal_directory() -> None:
    assert join_experiment_file("../etc", "results.json") is None


def test_join_experiment_file_rejects_an_absolute_directory() -> None:
    assert join_experiment_file("/experiments/exp_0", "results.json") is None


# -- validators --


def test_validate_non_empty_str_returns_the_value() -> None:
    assert validate_non_empty_str("focus on regularization", "recommended_focus", NODE) == (
        "focus on regularization"
    )


@pytest.mark.parametrize("value", [None, "", "   ", 3, ["a"]])
def test_validate_non_empty_str_rejects_blank_and_non_string(value: Any) -> None:
    with pytest.raises(ValueError, match="recommended_focus"):
        validate_non_empty_str(value, "recommended_focus", NODE)


def test_validate_enum_returns_the_token() -> None:
    assert validate_enum("overfitting", "root_cause", ROOT_CAUSES, NODE) == "overfitting"


@pytest.mark.parametrize("value", ["wrong family", "OVERFITTING", None, 1])
def test_validate_enum_rejects_a_token_outside_the_vocabulary(value: Any) -> None:
    with pytest.raises(ValueError, match="root_cause"):
        validate_enum(value, "root_cause", ROOT_CAUSES, NODE)


def test_validate_int_returns_the_value() -> None:
    assert validate_int(2, "priority", NODE) == 2


@pytest.mark.parametrize("value", [True, 1.0, "1", None])
def test_validate_int_rejects_bool_float_and_string(value: Any) -> None:
    with pytest.raises(ValueError, match="priority"):
        validate_int(value, "priority", NODE)


@pytest.mark.parametrize("value", [0.0, 0.5, 1, 1.0])
def test_validate_unit_interval_accepts_the_closed_unit_interval(value: Any) -> None:
    assert validate_unit_interval(value, "confidence", NODE) == float(value)


@pytest.mark.parametrize("value", [1.5, -0.1, True, float("nan"), float("inf"), "0.5", None])
def test_validate_unit_interval_rejects_out_of_range_bool_and_non_finite(value: Any) -> None:
    with pytest.raises(ValueError, match="confidence"):
        validate_unit_interval(value, "confidence", NODE)


def test_validate_str_list_returns_a_copy_of_the_entries() -> None:
    entries = ["fold spread 0.2", "baseline delta -0.01"]

    assert validate_str_list(entries, "evidence", NODE, min_len=1, max_len=8) == entries


@pytest.mark.parametrize("value", [[], "evidence", None, [1], ["ok", "  "]])
def test_validate_str_list_rejects_empty_non_list_and_blank_entries(value: Any) -> None:
    with pytest.raises(ValueError, match="evidence"):
        validate_str_list(value, "evidence", NODE, min_len=1, max_len=8)


def test_validate_str_list_rejects_more_than_max_len_entries() -> None:
    with pytest.raises(ValueError, match="evidence"):
        validate_str_list(["e"] * 9, "evidence", NODE, min_len=1, max_len=8)


def test_validate_object_list_returns_the_entries() -> None:
    entries = [{"order": 1}]

    assert validate_object_list(entries, "changes", NODE, min_len=1, max_len=6) == entries


@pytest.mark.parametrize("value", [[], None, {"order": 1}, [1], ["x"]])
def test_validate_object_list_rejects_empty_non_list_and_non_object_entries(value: Any) -> None:
    with pytest.raises(ValueError, match="changes"):
        validate_object_list(value, "changes", NODE, min_len=1, max_len=6)


def test_validate_rank_permutation_accepts_a_complete_ranking() -> None:
    assert validate_rank_permutation([3, 1, 2], "priority", NODE) is None


@pytest.mark.parametrize("values", [[1, 3], [1, 1, 2], [0, 1], [2], [-1, 1]])
def test_validate_rank_permutation_rejects_gaps_duplicates_and_zero(values: list[int]) -> None:
    with pytest.raises(ValueError, match="priority"):
        validate_rank_permutation(values, "priority", NODE)
