"""Shared JSON-extraction, degrade-safe artifact reading and validation helpers
for the three Pipeline Phase 6 (Evaluation) LLM nodes — `error_analyst`,
`hypothesis_generator` and `experiment_designer` (all T-032).

**Why one private module rather than three copies.** All three consumers land
in the same PR, so a single private module is strictly less duplication at zero
extra coupling — the same justification `src/nodes/compute/_evaluation_common.py`
carries for the two Phase 6 compute nodes.

**Why not `src/nodes/llm/base.py`.** The fence-stripping/JSON-extraction trio is
duplicated at eight call sites across `src/nodes/llm/` and hoisting it into
`base.py` would mean migrating eight landed node modules from a PR scoped to
three new nodes. That hoist is tracked as its own future task in
`context/discoveries/`; this module deliberately makes the copy count 8 -> 9,
not 8 -> 11.

**Why this module does not import `src/nodes/compute/_evaluation_common.py`.**
That module owns `resolve_output_iteration`/`candidate_experiment_dirs`, whose
adversarial review (T-031) fixed a real experiment-directory mislabeling bug.
Importing it from an LLM node is legal under CLAUDE.md invariant #8 (the ban is
on compute nodes importing LLM modules, not the reverse) but contradicts
T-031's documented ported-not-imported decoupling choice, and re-implementing
those functions here could reintroduce the bug the review fixed. So the resolved
experiment directory is never re-derived: it is read out of
`reports/score_evaluation_{iteration}.json`'s own `experiment_dir` field and
joined with a filename via `join_experiment_file`.

**Known fragility — the artifact filename number can diverge.**
`score_evaluator`/`feature_importance_extractor` name their reports from
`_evaluation_common.resolve_output_iteration` (derived from the `exp_{N}`
directory actually read), while `LLMNode._resolve_output_path` — and therefore
every read in this module — uses `state["current_iteration"]`. The two can
differ when the state-recorded experiment pointer is stale. That is exactly why
every reader here degrades to an explicit placeholder instead of raising: a
Phase 6 pass whose upstream report is filed under a different number must still
produce a diagnosis, not abort the graph run.

This module declares no class whose `name` matches its own filename stem
(`_evaluation_llm_common`), so `src/graph/node_resolver.py`'s `_find_node_class`
never mistakes it for a node module — see docs/pipeline.md § Node-module
convention. It is imported by the three node modules above and never referenced
in `config/phases/*.yaml`.

Every function takes the calling node's `node_name` so error messages attribute
a bad response to the node that produced it — the same convention
`_research_common.py` and `_experiment_design.py` use. Every validation failure
raises `ValueError` and nothing else.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from src.nodes.llm.base import relative_to_workspace
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

# Everything an upstream-artifact read may throw at a node that promises to
# degrade rather than abort the graph. Deliberately wide, and each member earns
# its place: `OSError` — file missing, unreadable, a directory. `ValueError` —
# `json.JSONDecodeError` (truncated/empty JSON) and `UnicodeDecodeError`
# (invalid UTF-8), both `ValueError` subclasses, plus `WorkspaceManager._resolve`/
# `Path.relative_to` rejecting a path that is absolute outside the workspace root
# or contains a `..` component. `RecursionError` — a pathologically nested
# payload (~993 levels) exhausts the interpreter's stack inside `json.loads`, and
# again inside `json.dumps` on the way back out; it is a `RuntimeError`, so
# neither of the other two catches it. Both the read *and* the re-serialization
# therefore sit inside the guard (`read_workspace_json` and
# `render_json_section`).
DEGRADE_ERRORS: tuple[type[Exception], ...] = (OSError, ValueError, RecursionError)

# design.md's root-cause vocabulary for Phase 6. `wrong_model_family` spells out
# the task file's shorthand "wrong family" to match `design.json`'s own
# `model_family` key. `cv_lb_divergence` is retained as design.md vocabulary but
# is constrained by the prompts: no leaderboard score exists anywhere in this
# pipeline yet (`LabState` has no LB field and the `kaggle_client` node that
# would fetch one is unbuilt), so it may only ever be selected from the CV and
# baseline evidence actually supplied — never from an invented LB number.
ROOT_CAUSES: tuple[str, ...] = (
    "overfitting",
    "underfitting",
    "cv_lb_divergence",
    "feature_quality",
    "wrong_model_family",
)

# Upstream artifacts these nodes read, and the artifacts they write. All are
# workspace-relative and numbered by `state["current_iteration"]` — see the
# module docstring's divergence note.
SCORE_EVALUATION_PATTERN = "reports/score_evaluation_{iteration}.json"
FEATURE_IMPORTANCE_PATTERN = "reports/feature_importance_{iteration}.json"
ERROR_DIAGNOSIS_PATTERN = "reports/error_diagnosis_{iteration}.json"
HYPOTHESES_PATTERN = "reports/hypotheses_{iteration}.json"

# Joined onto the score artifact's own `experiment_dir` field — never onto a
# re-derived directory. See `join_experiment_file`.
RESULTS_FILENAME = "results.json"
DESIGN_FILENAME = "design.json"

_UNRENDERABLE = "(unable to render this artifact as JSON)"


def _strip_outer_fence(content: str, node_name: str) -> str:
    """Strip a single outer fence wrapping the entire response, if present.

    Same outer-fence-anchoring approach as `_experiment_design._strip_outer_fence`:
    anchors on the outermost ``` markers only, so an embedded ``` inside a string
    value (e.g. a rationale quoting code) is never mistaken for the closing fence.
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    if not text.endswith("```") or len(text) < 6:
        raise ValueError(f"{node_name} response starts with a fence but never closes it")
    first_newline = text.find("\n")
    if first_newline == -1:
        raise ValueError(f"{node_name} response fence has no content")
    inner = text[first_newline + 1 :]
    closing_idx = inner.rfind("```")
    if closing_idx == -1:
        raise ValueError(f"{node_name} response fence has no closing delimiter")
    return inner[:closing_idx].strip()


def _parse_json(text: str, node_name: str) -> Any:
    # A bare `except ValueError` rather than `except json.JSONDecodeError`:
    # `json.loads` also raises a plain `ValueError` for an integer literal beyond
    # CPython's 4300-digit conversion limit, which `JSONDecodeError` would miss
    # and let escape unwrapped (and unattributed to a node).
    try:
        return json.loads(text)
    except ValueError as e:
        raise ValueError(f"{node_name} response is not valid JSON: {e}") from e


def _slice_outermost_braces(text: str) -> str | None:
    """The substring from the first `{` to the last `}`, or `None` if there isn't
    one — the salvage window for a response that wrapped its JSON in prose."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + 1]


def extract_json_object(content: str, node_name: str) -> dict[str, Any]:
    """Extract a top-level JSON object from an LLM response.

    Accepts raw JSON with no fence, or the entire response wrapped in a single
    ```json or unlabeled ``` fence. If **either** the fence handling or the parse
    fails, retries once on the substring between the first `{` and the last `}` —
    salvaging the three common wrapper failures: a sentence of preamble, a
    sentence of postamble after a closed fence, and a fence the response never
    closed. Raises `ValueError` naming `node_name` — carrying the *original*
    error, not the salvage attempt's — when the salvage is unavailable or also
    fails, or when the top-level value is not an object.

    Justified for these three nodes specifically because
    `config/phases/phase6_evaluation.yaml` declares `critic: null`: they have no
    retry wrapper at all, so a stray sentence of prose must not abort the run.

    Fail-closed by construction: the salvage only ever hands `json.loads` one
    contiguous substring of the response. It widens what reaches the validator;
    it never weakens what the validator accepts.
    """
    try:
        data = _parse_json(_strip_outer_fence(content, node_name), node_name)
    except ValueError as first_error:
        salvaged = _slice_outermost_braces(content)
        if salvaged is None or salvaged == content.strip():
            raise
        try:
            data = _parse_json(salvaged, node_name)
        except ValueError:
            raise first_error from None
    if not isinstance(data, dict):
        raise ValueError(f"{node_name} response must be a JSON object, got {type(data).__name__}")
    return data


def current_iteration(state: LabState) -> int:
    """`state["current_iteration"]` as a plain `int`, defaulting to `0`.

    `isinstance(True, int)` is `True` in Python, so a boolean would otherwise
    interpolate into an artifact path as `reports/..._True.json` — a silent
    contract violation rather than a visible failure. LangGraph does not enforce
    the `LabState` TypedDict at runtime, so this is a real (if unlikely) input.
    """
    raw = state.get("current_iteration")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return raw


def read_workspace_json(relative_path: Any, workspace: WorkspaceManager) -> dict[str, Any] | None:
    """Read a workspace JSON artifact as a dict, or `None` if it cannot be read.

    **Never raises.** Returns `None` when `relative_path` is not a non-empty
    string, when the read fails under `DEGRADE_ERRORS`, or when the parsed value
    is not a JSON object. Callers render the `None` as an explicit placeholder
    (see `render_json_section`) so the LLM is told the input is missing rather
    than being handed a silently empty section.

    `relative_to_workspace` is applied inside the guard: a resumed run can carry
    an absolute path recorded against a workspace root that has since moved, and
    that raises `ValueError`.
    """
    if not isinstance(relative_path, str) or not relative_path.strip():
        return None
    try:
        data = workspace.read_json(relative_to_workspace(relative_path, workspace))
    except DEGRADE_ERRORS:
        return None
    if not isinstance(data, dict):
        return None
    return data


def render_json_section(data: dict[str, Any] | None, missing_message: str) -> str:
    """Pretty-print an artifact for a prompt section, degrading to a placeholder.

    **Never raises.** `missing_message` is returned when the artifact could not
    be read at all; a fixed "unable to render" string is returned when the
    artifact was read but its re-serialization blows up — the serialization half
    of the degrade contract (a ~993-level nested payload exhausts the stack
    inside `json.dumps` just as readily as inside `json.loads`).
    """
    if data is None:
        return missing_message
    try:
        return json.dumps(data, indent=2)
    except DEGRADE_ERRORS:
        return _UNRENDERABLE


def join_experiment_file(experiment_dir: Any, filename: str) -> str | None:
    """Workspace-relative path to `filename` inside an already-resolved
    experiment directory, or `None` when the directory is unusable.

    `experiment_dir` comes from `reports/score_evaluation_{N}.json`'s own
    `experiment_dir` field — the directory `score_evaluator` actually read. This
    function never re-derives a directory from state, and never re-implements
    `_evaluation_common.resolve_output_iteration`/`candidate_experiment_dirs`
    (see the module docstring).

    Rejects a non-string, an empty string, an absolute path and any `..`
    component — `WorkspaceManager._resolve` would raise on all three, and this
    is a read of a value that flowed through an LLM-authored artifact.
    """
    if not isinstance(experiment_dir, str) or not experiment_dir.strip():
        return None
    candidate = Path(experiment_dir)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return f"{experiment_dir.rstrip('/')}/{filename}"


def validate_non_empty_str(value: Any, field: str, node_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{node_name} response field {field!r} must be a non-empty string, got {value!r}"
        )
    return value


def validate_enum(value: Any, field: str, allowed: tuple[str, ...], node_name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(
            f"{node_name} response field {field!r} must be one of {list(allowed)}, got {value!r}"
        )
    return value


def validate_int(value: Any, field: str, node_name: str) -> int:
    """`bool` is rejected explicitly: it is an `int` subclass, so `True` would
    otherwise pass as the rank `1` and silently corrupt an ordering."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{node_name} response field {field!r} must be an integer, got {value!r}")
    return value


def validate_unit_interval(value: Any, field: str, node_name: str) -> float:
    """A finite number in `[0.0, 1.0]`. Rejects `bool` (an `int` subclass, so
    `True` would read as a confidence of 1.0) and non-finite floats, which
    `WorkspaceManager.write_json` would serialize as the non-RFC-8259 tokens
    `Infinity`/`NaN`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{node_name} response field {field!r} must be a number in [0.0, 1.0], got {value!r}"
        )
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(
            f"{node_name} response field {field!r} must be a finite number in [0.0, 1.0], "
            f"got {value!r}"
        )
    return float(value)


def validate_str_list(
    value: Any, field: str, node_name: str, *, min_len: int, max_len: int
) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(
            f"{node_name} response field {field!r} must be a list of strings, got {value!r}"
        )
    if not min_len <= len(value) <= max_len:
        raise ValueError(
            f"{node_name} response field {field!r} must contain {min_len} to {max_len} "
            f"entries, got {len(value)}"
        )
    for i, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            raise ValueError(
                f"{node_name} response field {field!r} entry {i} must be a non-empty "
                f"string, got {entry!r}"
            )
    return list(value)


def validate_object_list(
    value: Any, field: str, node_name: str, *, min_len: int, max_len: int
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(
            f"{node_name} response field {field!r} must be a list of objects, got {value!r}"
        )
    if not min_len <= len(value) <= max_len:
        raise ValueError(
            f"{node_name} response field {field!r} must contain {min_len} to {max_len} "
            f"entries, got {len(value)}"
        )
    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{node_name} response field {field!r} entry {i} must be a JSON object, "
                f"got {entry!r}"
            )
    return list(value)


def validate_rank_permutation(values: list[int], field: str, node_name: str) -> None:
    """`values` must be exactly `1..len(values)` in some order — no gaps, no
    duplicates, no zero or negative rank. Same shape as
    `_research_common._validate_indices_cover_sources`: a partial ranking would
    make "prioritized" and "ordered" unenforceable downstream.
    """
    if sorted(values) != list(range(1, len(values) + 1)):
        raise ValueError(
            f"{node_name} response field {field!r} must be a permutation of "
            f"1..{len(values)}, got {values!r}"
        )
