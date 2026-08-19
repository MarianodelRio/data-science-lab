"""Unit tests for `src/nodes/llm/_delivery_common.py` — the private helper the
two Pipeline Phase 7 (Delivery) LLM nodes share.

Runs against a **real** `WorkspaceManager` over `tmp_path`: every function here
is pure file reading plus string rendering, so there is nothing to mock and no
network is involved. Mirrors `tests/unit/nodes/llm/test_evaluation_llm_common.py`.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

import src.nodes.llm._delivery_common as delivery_common
from src.nodes.llm._delivery_common import (
    BUDGET_EXHAUSTED,
    MISSING_FILE,
    build_markdown_artifact,
    fence_for,
    previous_iteration,
    read_bounded_texts,
    read_workspace_text,
    render_inputs_section,
    safe_relative,
    truncate,
)
from src.state import new_state
from src.workspace.workspace_manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


def _state(**overrides: Any) -> Any:
    state = new_state("comp", "/workspace")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def test_module_declares_no_class_matching_its_own_stem() -> None:
    """`node_resolver._find_node_class` looks for a class *defined in* the module
    whose `name` equals the module stem. This module declares no class at all, so
    it can never be mistaken for a node module."""
    classes = [
        obj
        for obj in vars(delivery_common).values()
        if inspect.isclass(obj) and obj.__module__ == delivery_common.__name__
    ]
    assert classes == []
    # `_find_node_class` scans the module namespace, not only what the module
    # defines, so the re-exported/imported names must not be class-shaped either.
    # (Asserting this over `classes` would be dead: `any([])` is always False.)
    namespace_classes = [obj for obj in vars(delivery_common).values() if inspect.isclass(obj)]
    assert [
        obj for obj in namespace_classes if getattr(obj, "name", None) == "_delivery_common"
    ] == []


# -- read_workspace_text --------------------------------------------------


def test_read_workspace_text_returns_the_content(workspace: WorkspaceManager) -> None:
    workspace.write_text("src/train.py", "import numpy\n")

    assert read_workspace_text("src/train.py", workspace) == "import numpy\n"


def test_read_workspace_text_returns_none_for_a_missing_file(workspace: WorkspaceManager) -> None:
    assert read_workspace_text("src/nope.py", workspace) is None


def test_read_workspace_text_returns_none_for_invalid_utf8(workspace: WorkspaceManager) -> None:
    target = workspace.workspace_path / "src" / "binary.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\xff\xfe\x00garbage")

    assert read_workspace_text("src/binary.py", workspace) is None


def test_read_workspace_text_returns_none_for_a_traversal_path(
    workspace: WorkspaceManager,
) -> None:
    assert read_workspace_text("../outside.py", workspace) is None


@pytest.mark.parametrize("value", [None, 0, [], "", "   "])
def test_read_workspace_text_returns_none_for_a_non_string_path(
    value: Any, workspace: WorkspaceManager
) -> None:
    assert read_workspace_text(value, workspace) is None


# -- safe_relative --------------------------------------------------------


def test_safe_relative_passes_through_a_relative_path(workspace: WorkspaceManager) -> None:
    assert safe_relative("experiments/exp_2", workspace) == "experiments/exp_2"


def test_safe_relative_relativizes_an_absolute_path_inside_the_workspace(
    workspace: WorkspaceManager,
) -> None:
    absolute = str(workspace.workspace_path / "experiments" / "exp_2")

    assert safe_relative(absolute, workspace) == "experiments/exp_2"


@pytest.mark.parametrize("value", ["../evil", "/elsewhere/exp_2", "", None, 7])
def test_safe_relative_returns_none_for_traversal_and_out_of_root(
    value: Any, workspace: WorkspaceManager
) -> None:
    assert safe_relative(value, workspace) is None


# -- previous_iteration ---------------------------------------------------


def test_previous_iteration_is_current_minus_one() -> None:
    assert previous_iteration(_state(current_iteration=3)) == 2


def test_previous_iteration_is_minus_one_for_a_fresh_state() -> None:
    """A standalone Phase 7 run never had a Phase 6 iteration; `-1` is the
    documented value and must NOT be clamped to 0."""
    assert previous_iteration(new_state("comp", "/workspace")) == -1


def test_previous_iteration_coerces_a_boolean_to_zero_base() -> None:
    assert previous_iteration(_state(current_iteration=True)) == -1


# -- fence_for / truncate -------------------------------------------------


def test_fence_for_is_longer_than_the_longest_backtick_run() -> None:
    assert fence_for("a\n```python\nprint(1)\n```\n") == "````"


def test_fence_for_minimum_is_three() -> None:
    assert fence_for("plain content, no backticks") == "```"


def test_truncate_leaves_short_text_unchanged() -> None:
    assert truncate("short", "src/train.py", 100) == "short"


def test_truncate_marks_long_text_with_its_label() -> None:
    result = truncate("x" * 50, "src/train.py", 10)

    assert result.startswith("x" * 10)
    assert "truncated at 10 characters of src/train.py" in result


# -- read_bounded_texts ---------------------------------------------------


def test_read_bounded_texts_reads_every_candidate_within_budget(
    workspace: WorkspaceManager,
) -> None:
    workspace.write_text("src/features.py", "features")
    workspace.write_text("src/models.py", "models")

    sections, read_map = read_bounded_texts(
        ["src/features.py", "src/models.py"], workspace, budget=1000
    )

    assert sections == [("src/features.py", "features"), ("src/models.py", "models")]
    assert read_map == {"src/features.py": True, "src/models.py": True}


def test_read_bounded_texts_truncates_the_file_that_overflows_the_budget(
    workspace: WorkspaceManager,
) -> None:
    workspace.write_text("src/train.py", "y" * 500)

    sections, read_map = read_bounded_texts(["src/train.py"], workspace, budget=100)

    rendered = sections[0][1]
    assert "truncated at 100 characters of src/train.py" in rendered
    assert len(rendered) < 500
    assert read_map == {"src/train.py": True}


def test_read_bounded_texts_omits_candidates_after_the_budget_is_exhausted(
    workspace: WorkspaceManager,
) -> None:
    workspace.write_text("src/features.py", "z" * 200)
    workspace.write_text("src/models.py", "never injected")

    sections, read_map = read_bounded_texts(
        ["src/features.py", "src/models.py"], workspace, budget=50
    )

    assert sections[1] == ("src/models.py", BUDGET_EXHAUSTED)
    assert read_map == {"src/features.py": True, "src/models.py": False}


def test_read_bounded_texts_records_a_missing_file_as_not_read(
    workspace: WorkspaceManager,
) -> None:
    workspace.write_text("src/models.py", "models")

    sections, read_map = read_bounded_texts(
        ["src/features.py", "src/models.py"], workspace, budget=1000
    )

    assert sections[0] == ("src/features.py", MISSING_FILE)
    assert read_map == {"src/features.py": False, "src/models.py": True}


def test_render_code_sections_fences_content_but_not_placeholders() -> None:
    rendered = delivery_common.render_code_sections(
        [("src/train.py", "print(1)"), ("src/models.py", MISSING_FILE)]
    )

    assert "### src/train.py\n\n```\nprint(1)\n```" in rendered
    assert f"### src/models.py\n\n{MISSING_FILE}" in rendered


# -- rendering ------------------------------------------------------------


def test_render_inputs_section_distinguishes_read_from_unavailable() -> None:
    rendered = render_inputs_section({"src/train.py": True, "src/models.py": False})

    assert "- `src/train.py` — read" in rendered
    assert "- `src/models.py` — not available" in rendered


def test_build_markdown_artifact_contains_title_body_and_inputs() -> None:
    artifact = build_markdown_artifact(
        "Code Review", "narrative body", "Files reviewed", {"src/train.py": True}
    )

    assert artifact.index("# Code Review") < artifact.index("narrative body")
    assert artifact.index("narrative body") < artifact.index("## Files reviewed")
    assert "- `src/train.py` — read" in artifact
