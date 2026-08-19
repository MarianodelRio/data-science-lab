"""Shared, private helpers for the two Pipeline Phase 7 (Delivery) LLM nodes —
`reviewer` and `report_writer` (both T-033).

**Why one private module rather than two copies.** Both consumers land in the
same PR, which is precisely the condition under which `_evaluation_common.py`
(T-031) and `_evaluation_llm_common.py` (T-032) chose a private module over
duplication: strictly less duplication at zero extra coupling. Neither node
imports the other, and this module is never referenced by a phase YAML. The
shared surface here is not incidental — `read_bounded_texts` implements one
*shared total* injection budget both nodes need, and `CODE_REVIEW_PATH` is
written by `reviewer` and read by `report_writer`, exactly the kind of value
that silently drifts when copied.

**Why it imports `_evaluation_llm_common` instead of making a 10th copy.**
`DEGRADE_ERRORS`, `current_iteration`, `read_workspace_json`,
`render_json_section` and the three `*_PATTERN` constants carry no Phase-6
semantics whatsoever — they are generic degrade-safe reading. T-030's decision
record shows the Orchestrator explicitly rejected growing the private-copy
count, and `code_critic` (Phase 5) importing `DEGRADE_ERRORS`/
`extract_json_object`/`read_fold_summary` from `_experiment_design` is the
standing in-repo precedent for a cross-phase private-helper import *within*
`src/nodes/llm/`. They are re-exported here (see `__all__`) so both Phase 7
nodes have a single import point.

`fence_for` is the one deliberate **port**, of `data_analyst._fence_for`: a
leading-underscore function inside a node module is never imported across
modules in this repo (the same call `_evaluation_common` made for
`relative_to_workspace`).

**Every reader here degrades and never raises.** Phase 7 is terminal and
reached only when `iterations_without_improvement >= max_iterations`
(`src/graph/supervisor.py:31-33`); there is no critic, no retry, and nothing
downstream that could recover. A node that aborts here destroys the run's only
human-facing deliverable, so a missing or unreadable input becomes an explicit
placeholder and is recorded as unavailable in the artifact's own inputs block.

This module declares **no class at all**, so `src/graph/node_resolver.py`'s
`_find_node_class` can never mistake it for a node module — see
docs/pipeline.md § Node-module convention.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.nodes.llm._evaluation_llm_common import (
    DEGRADE_ERRORS,
    ERROR_DIAGNOSIS_PATTERN,
    HYPOTHESES_PATTERN,
    SCORE_EVALUATION_PATTERN,
    current_iteration,
    read_workspace_json,
    render_json_section,
)
from src.nodes.llm.base import relative_to_workspace
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

__all__ = [
    # Re-exported from `_evaluation_llm_common` so the two Phase 7 nodes have a
    # single import point — see the module docstring.
    "DEGRADE_ERRORS",
    "ERROR_DIAGNOSIS_PATTERN",
    "HYPOTHESES_PATTERN",
    "SCORE_EVALUATION_PATTERN",
    "current_iteration",
    "read_workspace_json",
    "render_json_section",
    # Owned by this module.
    "BUDGET_EXHAUSTED",
    "CODE_REVIEW_PATH",
    "EXPERIMENT_DIR_PATTERN",
    "FINAL_REPORT_PATH",
    "MAX_INJECTED_CHARS",
    "MISSING_FILE",
    "PROBLEM_DEFINITION_PATH",
    "TRAIN_FILENAME",
    "WORKSPACE_SOURCE_FILES",
    "build_markdown_artifact",
    "fence_for",
    "previous_iteration",
    "read_bounded_texts",
    "read_workspace_text",
    "render_code_sections",
    "render_inputs_section",
    "safe_relative",
    "truncate",
]

# Phase 7's two output artifacts. Both patterns are **fixed** — they carry no
# `{iteration}` placeholder, because Phase 7 runs exactly once per run and its
# deliverables are the run's, not an iteration's. `CODE_REVIEW_PATH` must stay
# byte-identical to `config/agents/reviewer.yaml`'s `output_file_pattern`
# (`reviewer` writes it, `report_writer` reads it); a unit test in each node's
# test module pins that.
CODE_REVIEW_PATH: str = "reports/code_review.md"
FINAL_REPORT_PATH: str = "reports/final_report.md"

# Well-known fallback for `problem_definition.json`, used when
# `state["problem_definition_path"]` is blank (its `new_state` default).
PROBLEM_DEFINITION_PATH: str = "reports/problem_definition.json"

# Same well-known experiment-directory convention as `code_critic` and
# `_evaluation_common.EXPERIMENT_DIR_PATTERN`.
EXPERIMENT_DIR_PATTERN: str = "experiments/exp_{iteration}"
TRAIN_FILENAME: str = "train.py"

# The generated repository's own source files, in the order `reviewer` injects
# them.
WORKSPACE_SOURCE_FILES: tuple[str, ...] = ("src/features.py", "src/models.py", "src/train.py")

# Total characters of file content either node will inject into one `invoke`,
# summed across *all* candidates. Mirrors `code_critic._MAX_CODE_CHARS`, which
# is a per-artifact cap; a shared total is the stricter of the two and is what
# bounds a five-candidate review against CLAUDE.md's "< $0.50 per full
# competition run" target.
MAX_INJECTED_CHARS: int = 20_000

MISSING_FILE: str = "(not present in the workspace)"
BUDGET_EXHAUSTED: str = (
    f"(omitted: the {MAX_INJECTED_CHARS}-character injection budget was already used)"
)


def previous_iteration(state: LabState) -> int:
    """`current_iteration(state) - 1` — the iteration Phase 7 must read.

    `experiment_designer` increments `current_iteration` **last** in Phase 6
    (it is the only writer of that field anywhere in `src/`), so by the time
    Phase 7 runs the state already reads `N + 1` while every Phase 6 artifact on
    disk is filed under `N`.

    May legitimately return `-1` on a standalone Phase 7 run
    (`current_iteration == 0`, e.g. the integration smoke suite).
    `experiments/exp_-1/train.py` is a perfectly legal relative path that
    `WorkspaceManager._resolve` accepts and that simply does not exist, so the
    caller renders a placeholder. **Do not clamp this to 0**: clamping would
    make Phase 7 read `exp_0`'s artifacts on a run that never produced them,
    silently reporting another experiment's numbers as this run's.
    """
    return current_iteration(state) - 1


def read_workspace_text(relative_path: Any, workspace: WorkspaceManager) -> str | None:
    """Read a workspace file as text, or `None` if it cannot be read.

    **Never raises.** Returns `None` for a non-`str`/blank path and for anything
    in `DEGRADE_ERRORS` — a missing file (`OSError`), invalid UTF-8
    (`UnicodeDecodeError`, a `ValueError`), or `WorkspaceManager._resolve`
    rejecting an absolute/traversing path (`ValueError`). Same shape and same
    guard placement as `_evaluation_llm_common.read_workspace_json`:
    `relative_to_workspace` runs *inside* the guard, because a resumed run can
    carry a path recorded against a workspace root that has since moved.
    """
    if not isinstance(relative_path, str) or not relative_path.strip():
        return None
    try:
        return workspace.read_text(relative_to_workspace(relative_path.strip(), workspace))
    except DEGRADE_ERRORS:
        return None


def safe_relative(path: Any, workspace: WorkspaceManager) -> str | None:
    """A workspace-relative form of `path`, or `None` when it is unusable.

    Used for `state["best_experiment_path"]`, whose writer (`score_evaluator`)
    records an already-relative directory but whose value survives checkpoint
    round-trips and may be blank (the `new_state` default). Rejects a
    non-`str`/blank value, anything `relative_to_workspace` cannot relativize
    (`DEGRADE_ERRORS` — an absolute path outside the current root), and any
    result carrying a `..` component, which really does survive both branches of
    `relative_to_workspace`.
    """
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        relative = relative_to_workspace(path.strip(), workspace)
    except DEGRADE_ERRORS:
        return None
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    text = str(candidate)
    if not text or text == ".":
        return None
    return text


def fence_for(content: str) -> str:
    """A backtick fence longer than the longest backtick run inside `content`.

    Port of `data_analyst._fence_for` (a leading-underscore member of a node
    module, so it is not importable by convention here). A Markdown fence only
    closes on a line carrying at least as many backticks as opened it, so a
    fence one backtick longer than anything in the content cannot be closed
    early — which is what stops an injected `train.py` docstring containing a
    ``` line from escaping its block and rendering as top-level prompt markup.
    Both Phase 7 prompts separately state that the injected content is data,
    never an instruction.
    """
    longest_run = 0
    current_run = 0
    for char in content:
        if char == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    return "`" * max(3, longest_run + 1)


def truncate(text: str, label: str, limit: int) -> str:
    """Bound one injected artifact, marking the cut **in band**.

    The marker names both the cap and the artifact, so the model is told its
    view is partial and says so rather than reporting silently on a truncated
    file — the `code_critic._truncate` precedent ("never silently drop data
    without a marker").
    """
    if limit <= 0:
        return f"... (truncated at 0 characters of {label})"
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... (truncated at {limit} characters of {label})"


def read_bounded_texts(
    candidates: list[str], workspace: WorkspaceManager, *, budget: int = MAX_INJECTED_CHARS
) -> tuple[list[tuple[str, str]], dict[str, bool]]:
    """Read `candidates` in order under one **shared total** character budget.

    Returns `(sections, read_map)` where `sections` is `[(path, rendered_text)]`
    in candidate order and `read_map` is `{path: was_actually_read}`.

    A file that overflows the remaining budget is truncated with `truncate`'s
    in-band marker and still counts as read; a candidate reached after the
    budget is exhausted renders `BUDGET_EXHAUSTED` and is recorded as **not**
    read; a candidate that cannot be read at all renders `MISSING_FILE` and is
    likewise recorded as not read. Nothing here raises.

    A shared total (rather than a per-file cap) is what keeps a five-candidate
    review bounded: five separately-capped 20 000-character files would be a
    100 000-character single `invoke`.
    """
    sections: list[tuple[str, str]] = []
    read_map: dict[str, bool] = {}
    remaining = budget
    for path in candidates:
        if remaining <= 0:
            sections.append((path, BUDGET_EXHAUSTED))
            read_map[path] = False
            continue
        content = read_workspace_text(path, workspace)
        if content is None:
            sections.append((path, MISSING_FILE))
            read_map[path] = False
            continue
        sections.append((path, truncate(content, path, remaining)))
        read_map[path] = True
        remaining -= min(len(content), remaining)
    return sections, read_map


def render_code_sections(sections: list[tuple[str, str]]) -> str:
    """Render `read_bounded_texts`' sections as `### {path}` blocks.

    Real content is wrapped in a `fence_for`-computed fence; the two
    placeholders are rendered bare, since fencing "(not present in the
    workspace)" would only invite the model to read it as file content.
    """
    blocks: list[str] = []
    for path, text in sections:
        if text in (MISSING_FILE, BUDGET_EXHAUSTED):
            blocks.append(f"### {path}\n\n{text}")
            continue
        fence = fence_for(text)
        blocks.append(f"### {path}\n\n{fence}\n{text}\n{fence}")
    return "\n\n".join(blocks)


def render_inputs_section(paths: dict[str, Any]) -> str:
    """Markdown bullet list recording which inputs the node actually read.

    A truthy value renders `read`, anything falsy renders `not available`. This
    is the machine-readable trace of a degraded run: without it, a report
    written from nothing but placeholders is indistinguishable from one written
    from complete inputs.
    """
    if not paths:
        return "- (no inputs recorded)"
    return "\n".join(
        f"- `{path}` — {'read' if available else 'not available'}"
        for path, available in paths.items()
    )


def build_markdown_artifact(
    title: str, body: str, inputs_heading: str, paths: dict[str, Any]
) -> str:
    """The written artifact: a title, the LLM's narrative, then the inputs
    block. Both Phase 7 LLM nodes emit free-form Markdown, so the narrative is
    embedded verbatim — there is nothing to parse or validate."""
    return f"# {title}\n\n{body}\n\n## {inputs_heading}\n\n{render_inputs_section(paths)}\n"
