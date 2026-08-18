"""Shared, private helpers for the Pipeline Phase 6 (Evaluation) compute nodes
`score_evaluator.py` and `feature_importance_extractor.py`.

Declares no class whose `name` attribute matches its own module stem
(`_evaluation_common`), so `src.graph.node_resolver._find_node_class` never
mistakes this module for a node module — same convention as
`src/nodes/llm/_experiment_design.py`.

**Why a shared module rather than per-node duplication** (the convention the
Phase 5 specialists follow, each carrying its own copy of `design.json`
reading): that duplication convention exists because those readers are
duplicated *across separately-scheduled tasks landed by different agents* over
time — a shared dependency between tasks that ship independently would create
exactly the coupling `resolve_node`'s by-convention discovery is designed to
avoid. Here both consumers (`score_evaluator`, `feature_importance_extractor`)
land in the same task/PR, so one small private module is strictly less
duplication at zero extra coupling cost: neither node imports the other, and
this module is never referenced by a phase YAML.

Every reader here degrades to an empty/`None` result on I/O or parse failure —
never raises — because Phase 6 must remain invokable standalone (no upstream
phase has necessarily produced real artifacts yet, e.g. in the integration
smoke suite) and because a compute node crashing is worse than it reporting
"nothing to evaluate" (see `score_evaluator`'s own module docstring for how it
surfaces that distinction).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.workspace.workspace_manager import WorkspaceManager

# Same degrade-on set as `src.nodes.llm._experiment_design.DEGRADE_ERRORS`.
# `OSError` covers a missing/unreadable file, `ValueError` covers both
# `WorkspaceManager._resolve`'s traversal/absolute-path rejection and
# `Path.relative_to`'s "not a subpath" failure, and `RecursionError` covers a
# pathologically deep `json.loads` payload (not a `ValueError` subclass).
DEGRADE_ERRORS: tuple[type[Exception], ...] = (OSError, ValueError, RecursionError)

# Well-known experiment-directory pattern, mirroring `code_critic.py`'s
# `_EXPERIMENT_DIR_PATTERN` (`code_critic.py:91`) — the same "well-known
# fallback" convention this module ports for both Phase 6 nodes.
EXPERIMENT_DIR_PATTERN = "experiments/exp_{iteration}"
RESULTS_FILENAME = "results.json"
DESIGN_FILENAME = "design.json"


def relative_to_workspace(path: str, workspace: WorkspaceManager) -> str:
    """Local copy of `src.nodes.llm.base.relative_to_workspace`'s exact
    behavior (base.py:39-58) — an already-relative path passes through
    unchanged; an absolute path outside the workspace root raises `ValueError`
    via `Path.relative_to`.

    Cannot import the original: it lives in `src/nodes/llm/`, which
    transitively pulls in `langchain_core`, violating this module's (and both
    consuming nodes') "no LLM import" contract.
    """
    p = Path(path)
    if not p.is_absolute():
        return path
    return str(p.relative_to(workspace.workspace_path))


def read_json_dict(path: str, workspace: WorkspaceManager) -> dict[str, Any]:
    """Read `path` (a `LabState` path-field value, or a workspace-relative
    artifact path) as a JSON object, degrading to `{}` — never raising — on an
    unset/empty/non-`str` path, a missing or unreadable file, malformed JSON,
    an invalid relative path, or JSON content that parses but isn't an object.

    Same shape as `specialist_selector._read_json_dict`
    (`specialist_selector.py:150-163`).
    """
    if not isinstance(path, str) or not path:
        return {}
    try:
        relative_path = relative_to_workspace(path, workspace)
        # `json.JSONDecodeError` is itself a `ValueError` subclass, so it is
        # already covered by `DEGRADE_ERRORS` — no separate clause needed.
        data = workspace.read_json(relative_path)
    except DEGRADE_ERRORS:
        return {}
    return data if isinstance(data, dict) else {}


def experiment_dir_from_state(state: dict[str, Any], workspace: WorkspaceManager) -> str | None:
    """Workspace-relative experiment *directory* derived from
    `state["experiments"][-1]["path"]`, or `None` when that pointer is absent
    or unusable.

    Verbatim port of `code_critic._experiment_dir_from_state`
    (`code_critic.py:99-144`): re-relativizes the recorded path (degrading to
    `None` on `DEGRADE_ERRORS` — e.g. a path recorded against a different
    workspace root), rejects `..`-traversal, and takes `.parent` when the
    candidate carries a file suffix (so both "records the directory" and
    "records the file inside it" conventions work).
    """
    experiments = state.get("experiments")
    if not isinstance(experiments, list) or not experiments:
        return None
    last = experiments[-1]
    if not isinstance(last, dict):
        return None
    raw_path = last.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None

    try:
        reference = relative_to_workspace(raw_path.strip(), workspace)
    except DEGRADE_ERRORS:
        return None

    candidate = Path(reference)
    if ".." in candidate.parts:
        return None
    if candidate.suffix:
        candidate = candidate.parent
    text = str(candidate)
    if not text or text == ".":
        return None
    return text


def candidate_experiment_dirs(state: dict[str, Any], workspace: WorkspaceManager) -> list[str]:
    """Experiment directories to try, best guess first: the state-recorded
    pointer (when usable), then the well-known iteration directory.

    Verbatim port of `code_critic._candidate_experiment_dirs`
    (`code_critic.py:146-158`), against this module's own
    `EXPERIMENT_DIR_PATTERN`. The well-known fallback is always a candidate,
    so this always yields at least one entry.
    """
    iteration = state.get("current_iteration", 0)
    candidates = [
        experiment_dir_from_state(state, workspace),
        EXPERIMENT_DIR_PATTERN.format(iteration=iteration),
    ]
    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def read_experiment_results(
    state: dict[str, Any], workspace: WorkspaceManager
) -> tuple[str, dict[str, Any]]:
    """Try each candidate experiment directory's `results.json` in order,
    returning `(directory, results)` for the first candidate that yields a
    non-empty dict. If none do, returns `(candidates[0], {})` so the caller
    always has a directory to record — mirrors `code_critic`'s "first
    candidate names the place" convention.
    """
    candidates = candidate_experiment_dirs(state, workspace)
    for directory in candidates:
        results = read_json_dict(f"{directory}/{RESULTS_FILENAME}", workspace)
        if results:
            return directory, results
    return candidates[0], {}


def _coerce_iteration(value: Any) -> int | None:
    """`int`, but not `bool` (a `bool` is a structural `int` subclass in
    Python — `isinstance(True, int)` is `True` — and a boolean iteration
    number would be a silent contract violation)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def resolve_iteration(state: dict[str, Any]) -> int:
    """The iteration number Phase 6 artifacts are named for: the last
    experiment entry's own `iteration` key when present and a valid
    non-bool `int`, else `state["current_iteration"]` coerced the same way
    and defaulting to `0`.
    """
    experiments = state.get("experiments")
    if isinstance(experiments, list) and experiments:
        last = experiments[-1]
        if isinstance(last, dict):
            entry_iteration = _coerce_iteration(last.get("iteration"))
            if entry_iteration is not None:
                return entry_iteration

    return _coerce_iteration(state.get("current_iteration", 0)) or 0
