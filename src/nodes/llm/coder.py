"""coder: writes the training script for the current Phase 5 (Implementation)
experiment — the only node in this pipeline that writes ML implementation code.

Runs between `specialist_selector` and `code_critic` in
`config/phases/phase5_implementation.yaml`'s `sequence`. Reads the specialist's
`experiments/exp_{iteration}/design.json`, the Phase 4 `feature_spec.json`, and
the target column name from the Phase 3 `experiments/baseline/design.json`
(the only place `target_column` is ever written — see `_read_target_column`),
generates a training script via the LLM, writes it to
`experiments/exp_{iteration}/train.py`, executes it for real through
`code_executor.execute`, and validates the resulting artifacts
(`results.json`/`submission.csv`/OOF predictions). On any failure — a
malformed LLM response, a nonzero exit, or a missing/invalid artifact — it
re-prompts the LLM with the failure reason and stderr, bounded by
`_MAX_EXECUTION_RETRIES`. If the budget is exhausted, it raises `ValueError`;
there is no forced-pass concept at this layer (that is `code_critic`'s
separate design-quality loop one level up).

Overrides `LLMNode.__call__` wholesale — same precedent as `code_critic`
(see its module docstring): the execute-then-re-prompt loop needs the raw
`messages`/`response` pair to persist across cycles, which the base class's
narrow `_write_output(workspace, relative_path, response)` hook cannot
support.

**Test patch points.** This custom `__call__` instantiates `WorkspaceManager`
directly in this module (not via the base class's own `__call__`), so unit
tests must patch `src.nodes.llm.coder.WorkspaceManager`. It also calls
`Settings.load()` directly (for the `optuna`/`mlflow` run configuration
injected into the prompt), so tests must patch `src.nodes.llm.coder.Settings`
*in addition to* `src.nodes.llm.base.Settings` (the latter is still read by
the inherited `LLMNode.__init__` for `_max_messages_per_node`). `execute` is
imported into this module (`from src.tools.code_executor import execute`), so
tests patch `src.nodes.llm.coder.execute` — never the real subprocess.

**How `code_critic`'s outer retry loop composes with this one.** `code_critic`
re-invokes `coder` by calling `resolve_node("coder")(working_state)`, where
`working_state["messages"]` already carries the critic's own verdict
`AIMessage` as the last item. This node's first step —
`trim_context(state.get("messages", []), self._max_messages_per_node)` — picks
that message up and threads it into the outgoing `messages` list, so critic
feedback reaches the LLM with no extra plumbing.

**`state["experiments"]` has no LangGraph reducer** (a plain `LastValue`
channel, see `src/state.py`): this node reads the existing list, copies it,
appends exactly one entry, and returns the whole list. `coder.__call__` may
run internally up to `1 + _MAX_EXECUTION_RETRIES` times (its own retry loop),
and separately `code_critic` may re-invoke the *entire* `coder.__call__` up to
its own `max_retries` times — but only the first graph-level `coder` call's
returned delta is ever applied to the real `LabState` (LangGraph applies a
node's return value once per graph step; `code_critic`'s internal
re-invocations bypass the graph entirely and only mutate its own local
`working_state`). So `state["experiments"]` gains exactly one entry per
graph-level iteration, no matter how many times either loop runs internally.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.config.settings import Settings
from src.nodes.llm._experiment_design import (
    DEGRADE_ERRORS,
    read_fold_summary,
    resolve_feature_spec_ref,
)
from src.nodes.llm.base import LLMNode, trim_context
from src.state import LabState
from src.tools.code_executor import ExecResult, execute
from src.workspace.workspace_manager import WorkspaceManager

_TRAIN_FILENAME = "train.py"
_RESULTS_FILENAME = "results.json"
_SUBMISSION_FILENAME = "submission.csv"
_OOF_FALLBACK_FILENAME = "oof_predictions.parquet"
_DESIGN_FILENAME = "design.json"

# The one place `target_column` is ever written in this codebase:
# `baseline_designer` (Pipeline Phase 3) writes it here, and `baseline_runner`
# (a different node, same phase) reads it back. Phase 3 always runs before
# Phase 5's first iteration (CLAUDE.md invariant #4), so this artifact is
# expected to already exist by the time `coder` runs in any real pipeline
# execution — but this reader still degrades rather than raises (see
# `_read_target_column`) so Phase 5 stays invokable standalone.
_BASELINE_DESIGN_PATH = "experiments/baseline/design.json"

_TARGET_COLUMN_UNAVAILABLE = f"(target_column not available from {_BASELINE_DESIGN_PATH})"

# Bounds coder's own execute-then-re-prompt loop (CLAUDE.md invariant #5, one
# level down from code_critic's separate design-quality retry budget). Total
# LLM calls per graph-level `coder` invocation is at most this plus one.
_MAX_EXECUTION_RETRIES = 2

# `results.json["metric"]`, when present, must separator-normalize (see
# `_normalize_metric`) to one of these. Kept intentionally small: these are
# the metric families `score_evaluator` (T-031) already knows how to compare.
_VALID_METRICS = frozenset({"accuracy", "r2", "rsquared", "score"})

# Matches a single fenced code block, optionally labeled ```python. `findall`
# over this pattern is how `_extract_code` counts fences: zero or more than
# one is a malformed response, exactly one is the whole script.
_CODE_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def _extract_code(content: str) -> str:
    """The single fenced ```python script in `content`.

    Raises `ValueError` when the response contains zero fenced blocks, more
    than one (the output contract allows exactly one — no narrative code
    samples, no "here's an alternative" second block), or a block that is
    present but empty after stripping.
    """
    matches = _CODE_FENCE_RE.findall(content)
    if not matches:
        raise ValueError("coder response contains no fenced ```python code block")
    if len(matches) > 1:
        raise ValueError(
            f"coder response contains {len(matches)} fenced code blocks; expected exactly one"
        )
    code = matches[0].strip()
    if not code:
        raise ValueError("coder response's fenced code block is empty")
    return code


def _normalize_metric(value: str) -> str:
    """Separator/case-insensitive form of a metric name: `"R-Squared"` and
    `"r_squared"` and `"rsquared"` all normalize to `"rsquared"`."""
    return re.sub(r"[-_\s]+", "", value.strip().lower())


def _read_design(exp_dir: str, workspace: WorkspaceManager) -> dict[str, Any]:
    """`experiments/exp_{iteration}/design.json`, or `{}` when it cannot be
    read — never raises. See `DEGRADE_ERRORS` for the caught set."""
    try:
        design = workspace.read_json(f"{exp_dir}/{_DESIGN_FILENAME}")
    except DEGRADE_ERRORS:
        return {}
    return design if isinstance(design, dict) else {}


def _read_target_column(workspace: WorkspaceManager) -> str:
    """The target column name from `experiments/baseline/design.json`, or a
    placeholder when it cannot be read — never raises. See `DEGRADE_ERRORS`
    for the caught set.

    This is the *only* place the generated training script can learn which
    column to exclude from the feature matrix; without it, target-column
    identity would have to be guessed with zero grounded signal, and a wrong
    guess produces a plausible-looking but silently leaked/corrupted
    `cv_score` (see the T-020 precedent this mirrors: the same failure class,
    one layer up).
    """
    try:
        design = workspace.read_json(_BASELINE_DESIGN_PATH)
    except DEGRADE_ERRORS:
        return _TARGET_COLUMN_UNAVAILABLE
    if not isinstance(design, dict):
        return _TARGET_COLUMN_UNAVAILABLE
    target_column = design.get("target_column")
    if not isinstance(target_column, str) or not target_column.strip():
        return _TARGET_COLUMN_UNAVAILABLE
    return target_column


def _read_feature_spec(feature_spec_ref: str, workspace: WorkspaceManager) -> str:
    """Pretty-printed `feature_spec.json` text, or a placeholder when it
    cannot be read — never raises. See `DEGRADE_ERRORS` for the caught set."""
    try:
        data = workspace.read_json(feature_spec_ref)
    except DEGRADE_ERRORS:
        return f"(unable to read feature spec at {feature_spec_ref})"
    return json.dumps(data, indent=2)


def _oof_artifact_exists(
    workspace: WorkspaceManager, exp_dir: str, results: dict[str, Any]
) -> bool:
    """Whether the OOF predictions file the script claims to have written
    actually exists on disk.

    When `results["oof_path"]` is a usable string, it is re-relativized
    against the workspace root and checked directly — this honors a custom
    path the script chose. Any other value (absent, non-string, escapes the
    workspace) falls back to checking the well-known fallback filename inside
    `exp_dir`, satisfying the "write to this exact name" convention.

    The containment check resolves symlinks before the final `.exists()`
    check on both sides: a generated script could otherwise set `oof_path` to
    a symlink that sits inside the experiment directory (so it passes the
    `..`/absolute-path checks above) but whose target resolves outside the
    workspace root, e.g. into a caller-writable temp directory. Resolving
    first closes that gap.
    """
    oof_path = results.get("oof_path")
    if isinstance(oof_path, str) and oof_path.strip():
        candidate = Path(oof_path.strip())
        if candidate.is_absolute():
            try:
                candidate = candidate.relative_to(workspace.workspace_path)
            except ValueError:
                return False
        if ".." in candidate.parts:
            return False
        resolved = (workspace.workspace_path / candidate).resolve()
        if not resolved.is_relative_to(workspace.workspace_path.resolve()):
            return False
        return resolved.exists()
    return (workspace.workspace_path / exp_dir / _OOF_FALLBACK_FILENAME).exists()


def _validate_run(workspace: WorkspaceManager, exp_dir: str, exec_result: ExecResult) -> str:
    """Full-success gate for one execution attempt: `""` on success, else a
    human-readable failure reason fed back to the LLM as the next prompt.

    Checks, in order, subprocess-level failure first and artifact-level
    failure second, so the earliest real cause is always the one reported:
    timeout, nonzero exit, `results.json` unreadable/not-an-object,
    `cv_score` missing/non-numeric/non-finite, `metric` (if present)
    out-of-vocabulary, `submission.csv` missing, OOF artifact missing.
    """
    if exec_result.timed_out:
        return "train.py timed out during execution"
    if exec_result.returncode != 0:
        return f"train.py exited with code {exec_result.returncode}"

    try:
        results = workspace.read_json(f"{exp_dir}/{_RESULTS_FILENAME}")
    except (OSError, ValueError) as exc:
        return f"{_RESULTS_FILENAME} could not be read as JSON: {exc}"
    if not isinstance(results, dict):
        return f"{_RESULTS_FILENAME} must be a JSON object, got {type(results).__name__}"

    cv_score = results.get("cv_score")
    if cv_score is None or isinstance(cv_score, bool) or not isinstance(cv_score, (int, float)):
        return f"{_RESULTS_FILENAME}['cv_score'] must be a number, got {cv_score!r}"
    if not math.isfinite(cv_score):
        return f"{_RESULTS_FILENAME}['cv_score'] must be finite, got {cv_score!r}"

    metric = results.get("metric")
    if metric is not None and (
        not isinstance(metric, str) or _normalize_metric(metric) not in _VALID_METRICS
    ):
        return f"{_RESULTS_FILENAME}['metric'] {metric!r} is not one of {sorted(_VALID_METRICS)}"

    if not (workspace.workspace_path / exp_dir / _SUBMISSION_FILENAME).exists():
        return f"{_SUBMISSION_FILENAME} was not written to {exp_dir}"

    if not _oof_artifact_exists(workspace, exp_dir, results):
        return (
            f"no OOF predictions artifact found (expected {exp_dir}/{_OOF_FALLBACK_FILENAME} "
            "or a valid results.json['oof_path'])"
        )

    return ""


def _build_task_message(
    design_text: str,
    feature_spec_text: str,
    folds_summary: str,
    target_column: str,
    exp_dir: str,
    optuna_n_trials: int,
    optuna_early_stopping_patience: int,
    mlflow_tracking_uri: str,
) -> str:
    """The one HumanMessage `coder` receives after the system prompt: the
    design, the feature spec, a fold summary, the target column, the literal
    run configuration, and the exact output contract — see
    `config/prompts/coder/v1.md`."""
    return (
        f"## Experiment design (design.json)\n\n{design_text}\n\n"
        f"## Feature spec (feature_spec.json)\n\n{feature_spec_text}\n\n"
        f"## Frozen CV folds\n\n{folds_summary}\n\n"
        f"## Target column\n\n{target_column}\n\n"
        "## Run configuration\n\n"
        f"- optuna_n_trials: {optuna_n_trials}\n"
        f"- optuna_early_stopping_patience: {optuna_early_stopping_patience}\n"
        f"- mlflow_tracking_uri: {mlflow_tracking_uri}\n\n"
        "## Output contract\n\n"
        "Write exactly these three artifacts, workspace-relative from the script's own cwd:\n"
        f"- `{exp_dir}/{_RESULTS_FILENAME}`\n"
        f"- `{exp_dir}/{_SUBMISSION_FILENAME}`\n"
        f"- `{exp_dir}/{_OOF_FALLBACK_FILENAME}`\n"
    )


def _failure_message(reason: str, exec_result: ExecResult | None) -> str:
    """The next HumanMessage sent back to the LLM after a failed attempt:
    the failure reason plus stderr (when there is an `exec_result` — a
    malformed-response failure has none), and an explicit instruction to
    return the complete corrected script, not a patch."""
    stderr = exec_result.stderr if exec_result is not None else ""
    stderr_block = f"```\n{stderr}\n```" if stderr.strip() else "(no stderr captured)"
    return (
        f"The previous attempt failed: {reason}\n\n"
        f"## stderr\n\n{stderr_block}\n\n"
        "Return the complete corrected script as a single fenced ```python block — "
        "the whole file, not a diff or a partial patch."
    )


class CoderNode(LLMNode):
    name = "coder"

    def __call__(self, state: LabState) -> dict[str, Any]:
        workspace = WorkspaceManager(state["workspace_path"])
        settings = Settings.load()
        iteration = state["current_iteration"]

        train_relative_path = self._resolve_output_path(state)
        exp_dir = str(Path(train_relative_path).parent)

        design = _read_design(exp_dir, workspace)
        design_text = json.dumps(design, indent=2) if design else "(design.json not available)"
        feature_spec_ref = resolve_feature_spec_ref(state, workspace)
        feature_spec_text = _read_feature_spec(feature_spec_ref, workspace)
        folds_summary = read_fold_summary(state, workspace)
        target_column = _read_target_column(workspace)

        trimmed = trim_context(state.get("messages", []), self._max_messages_per_node)
        task_message = HumanMessage(
            content=_build_task_message(
                design_text,
                feature_spec_text,
                folds_summary,
                target_column,
                exp_dir,
                settings.optuna.n_trials,
                settings.optuna.early_stopping_patience,
                settings.workspace.mlflow_tracking_uri,
            )
        )
        messages: list[BaseMessage] = [
            SystemMessage(content=self.system_prompt),
            *trimmed,
            task_message,
        ]

        accumulated: list[BaseMessage] = []
        failure_reason = ""
        exec_result: ExecResult | None = None

        for attempt in range(_MAX_EXECUTION_RETRIES + 1):
            response = self.llm.invoke(messages)
            accumulated.append(response)
            content = (
                response.content if isinstance(response.content, str) else str(response.content)
            )

            try:
                code = _extract_code(content)
            except ValueError as exc:
                failure_reason, exec_result = str(exc), None
            else:
                workspace.write_text(train_relative_path, code)
                exec_result = execute(code, cwd=str(workspace.workspace_path))
                failure_reason = _validate_run(workspace, exp_dir, exec_result)

            if not failure_reason:
                break
            if attempt < _MAX_EXECUTION_RETRIES:
                messages = [
                    *messages,
                    response,
                    HumanMessage(content=_failure_message(failure_reason, exec_result)),
                ]

        if failure_reason:
            raise ValueError(
                f"coder: {_TRAIN_FILENAME} for {exp_dir} failed after "
                f"{_MAX_EXECUTION_RETRIES + 1} attempt(s); last failure: {failure_reason}"
            )

        results = workspace.read_json(f"{exp_dir}/{_RESULTS_FILENAME}")
        cv_score = float(results["cv_score"])

        experiments = state.get("experiments")
        experiments_list = list(experiments) if isinstance(experiments, list) else []
        experiments_list.append(
            {
                "id": f"exp_{iteration}",
                "path": exp_dir,
                "cv_score": cv_score,
                "iteration": iteration,
                "model": design.get("model_family", "unknown")
                if isinstance(design, dict)
                else "unknown",
            }
        )

        return {"messages": accumulated, "experiments": experiments_list}
