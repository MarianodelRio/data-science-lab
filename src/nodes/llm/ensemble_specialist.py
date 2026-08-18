"""ensemble_specialist: reads the solution plan, the frozen fold summary and the
out-of-fold (OOF) predictions of prior experiments, and designs one
stacking/blending/weighted-average combiner — writing
experiments/exp_{iteration}/design.json.

The `design.json` schema itself lives in `src/nodes/llm/_experiment_design.py`,
shared with the other Pipeline Phase 5 specialists and their `coder` consumer.
This node alone extends that eight-key contract with a ninth key,
`base_experiments`, via `ENSEMBLE_DESIGN_KEYS`/`validate_ensemble_design` — a
thin wrapper, not a change to `DESIGN_KEYS`/`validate_experiment_design`
themselves, which the four other landed specialists still rely on unchanged.

Overrides `_build_messages` (inject the solution plan, the frozen fold summary,
the feature-spec reference and the node-injected `base_experiments` as an extra
HumanMessage) and `_write_output` (extract + validate the JSON payload, write it
via `workspace.write_json`). Does NOT override `_build_output_state` — `coder`
(T-029) reads `experiments/exp_{iteration}/design.json` from its well-known
path directly, the same convention `baseline_designer`/`fold_config.json`
already use, so no new `LabState` field is needed (`src/state.py` is a
protected contract).

**`base_experiments` is node-injected, never read from the LLM response** —
the same convention `feature_spec_ref`/`cv_strategy_ref` already use. It is
built from `state["experiments"]` (all entries, in order — never filtered or
reordered) by resolving, for each entry, an `experiment_id` and the
workspace-relative path of its out-of-fold predictions. This node never
self-gates on "are there enough experiments to ensemble" either:
`specialist_selector` (T-023) already confirmed `state["experiments"]` has
>= 2 entries and a non-"no ensembling" `ensembling_strategy` before dispatching
here, so by the time this node runs the eligibility decision has already been
made. The schema itself only requires a **non-empty** `base_experiments`
(mirroring `search_space`'s "must not be empty" floor) — the `>= 2` eligibility
rule is `specialist_selector._should_ensemble`'s routing decision, not this
node's or the shared validator's to re-derive.

**OOF discovery convention.** For each `state["experiments"]` entry this node
reads that experiment's own `results.json` and uses its `oof_path` field when
present and it re-relativizes cleanly (rejecting `..`/absolute-outside-
workspace); otherwise it falls back to the `oof_predictions.parquet` file in
that experiment's directory. Binding on whoever writes `results.json`
(`coder`, T-029) — see `context/discoveries.md`.

**Runtime-unreachable today.** Nothing in `src/` writes `state["experiments"]`
yet (`coder`, its only producer, is T-029 and is blocked), so
`specialist_selector` can never actually satisfy its own `>= 2` eligibility
check in a real run and this node is never dispatched to in practice. It is
fully implemented and tested against a directly-constructed `state`, exactly
as `code_critic` (T-030) documents for its own well-known-path reads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm._experiment_design import (
    DEGRADE_ERRORS,
    extract_json_object,
    read_fold_summary,
    read_solution_plan,
    resolve_feature_spec_ref,
    validate_ensemble_design,
)
from src.nodes.llm.base import LLMNode, relative_to_workspace
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

_SPECIALIST = "ensemble_specialist"
_EXPERIMENT_DIR_PATTERN = "experiments/exp_{iteration}"
_RESULTS_FILENAME = "results.json"
_OOF_FALLBACK_FILENAME = "oof_predictions.parquet"
_NO_BASE_EXPERIMENTS = "(no base experiments recorded in state)"

# Canonical model family -> phrases that denote it. Matched as whole phrases with
# word boundaries against a separator-normalized copy of the LLM's `model_family`
# string (see `_experiment_design.normalize_model_family`). Human checkpoint
# decision: exactly three canonical families.
#
# Deliberately NOT aliased, in either direction: a bare "weighted", "weight",
# "stack" or "stacked" modifier. `normalize_model_family` has no longest-match-wins
# rule (2026-08-12 T-026 discovery, unchanged by this task), so a bare modifier
# alone cannot discriminate which family it qualifies — the same "bare generic
# modifier not aliased" technique `timeseries_specialist` uses for a bare "linear".
# Only the qualified multi-word forms are aliased. This makes "weighted blend of
# stacked models" resolve to `blending` alone (its only self-match alias is
# "weighted blend"; "stacked" is not aliased anywhere). It deliberately CANNOT
# defuse "blended stacking" — both "blended" (an alias of `blending`) and
# "stacking" (a self-match alias of `stacking`) are real canonical aliases, so
# that phrase is structurally ambiguous and raises "ambiguous" by design; the
# prompt's § model_family section states this rejection explicitly and pushes the
# nuance into `rationale` instead.
_MODEL_FAMILIES: dict[str, tuple[str, ...]] = {
    "stacking": (
        "stacking",
        "stacked ensemble",
        "stack ensemble",
        "stacked generalization",
        "stackingregressor",
        "stackingclassifier",
        "stackingcvregressor",
        "stackingcvclassifier",
        "meta learner",
        "metalearner",
        "meta model",
        "metamodel",
        "super learner",
        "superlearner",
        "second level model",
        "second stage model",
    ),
    "blending": (
        "blending",
        "blend",
        "blended",
        "blends",
        "blended ensemble",
        "holdout blend",
        "hold out blend",
        "holdout blending",
        "weighted blend",
        "weighted blending",
        "linear blend",
        "convex blend",
    ),
    "weighted_average": (
        "weighted average",
        "weighted averaging",
        "weighted mean",
        "weighted sum",
        "weight optimization",
        "optimal weights",
        "learned weights",
        "tuned weights",
        "convex combination",
        "linear combination of predictions",
    ),
}


def _fallback_iteration(entry: dict[str, Any], index: int) -> Any:
    """The `{iteration}` value used to build the fallback experiment directory
    for one `state["experiments"]` entry: the entry's own recorded `iteration`
    when it is a real (non-bool) int, else the entry's position in the list.

    Preferring the entry's own `iteration` over its list position keeps the
    fallback meaningful when experiments are read out of order or a later
    entry is missing its `path`: it still points at *that* experiment's own
    well-known directory rather than an arbitrary positional one.
    """
    raw_iteration = entry.get("iteration")
    if isinstance(raw_iteration, int) and not isinstance(raw_iteration, bool):
        return raw_iteration
    return index


def _experiment_dir_from_entry(
    entry: dict[str, Any], index: int, workspace: WorkspaceManager
) -> str:
    """Workspace-relative experiment *directory* for one `state["experiments"]`
    entry, or the well-known `experiments/exp_{iteration}` fallback when the
    entry's `path` is missing, absolute-outside-workspace, or contains `..`.

    Local duplication of `code_critic._experiment_dir_from_state`'s
    normalization (relativize, a value with a suffix is treated as a file
    pointer and its parent taken, reject `..`), adapted to operate on one
    entry directly rather than `state["experiments"][-1]`. Sibling LLM node
    modules never import from each other (context/decisions.md T-022/T-024/
    T-025), so this is a deliberate duplication, not a missed hoist.
    """
    fallback = _EXPERIMENT_DIR_PATTERN.format(iteration=_fallback_iteration(entry, index))
    raw_path = entry.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return fallback
    try:
        reference = relative_to_workspace(raw_path.strip(), workspace)
    except DEGRADE_ERRORS:
        return fallback
    candidate = Path(reference)
    if ".." in candidate.parts:
        return fallback
    if candidate.suffix:
        candidate = candidate.parent
    text = str(candidate)
    if not text or text == ".":
        return fallback
    return text


def _experiment_id(entry: dict[str, Any], index: int) -> str:
    """`entry["id"]` when it is a non-empty string, else `experiment_{index}` —
    the same "position-numbered fallback" convention `_EXPERIMENT_DIR_PATTERN`
    uses for a missing directory pointer."""
    experiment_id = entry.get("id")
    if isinstance(experiment_id, str) and experiment_id:
        return experiment_id
    return f"experiment_{index}"


def _oof_path_for_experiment(entry: dict[str, Any], index: int, workspace: WorkspaceManager) -> str:
    """The workspace-relative out-of-fold predictions path for one base
    experiment: `results.json`'s `oof_path` field when present and it
    re-relativizes cleanly (rejecting `..`/absolute-outside-workspace),
    else the `oof_predictions.parquet` convention in that experiment's own
    directory. Never raises — every read is guarded by `DEGRADE_ERRORS`, the
    same convention `_experiment_design.py`'s own readers use, because a
    missing or malformed `results.json` for one base experiment must not
    abort the whole ensemble design.
    """
    directory = _experiment_dir_from_entry(entry, index, workspace)
    fallback = f"{directory}/{_OOF_FALLBACK_FILENAME}"
    try:
        results = workspace.read_json(f"{directory}/{_RESULTS_FILENAME}")
    except DEGRADE_ERRORS:
        return fallback
    if not isinstance(results, dict):
        return fallback
    raw_oof_path = results.get("oof_path")
    if not isinstance(raw_oof_path, str) or not raw_oof_path.strip():
        return fallback
    try:
        reference = relative_to_workspace(raw_oof_path.strip(), workspace)
    except DEGRADE_ERRORS:
        return fallback
    candidate = Path(reference)
    if ".." in candidate.parts:
        return fallback
    text = str(candidate)
    if not text or text == ".":
        return fallback
    return text


def _build_base_experiments(state: LabState, workspace: WorkspaceManager) -> list[dict[str, str]]:
    """Node-injected `base_experiments`: one `{"experiment_id", "oof_path"}` per
    `state["experiments"]` entry, in order — **all** entries, never filtered,
    reordered, or influenced by the LLM (same convention as `feature_spec_ref`/
    `cv_strategy_ref`). Returns `[]` when `state["experiments"]` isn't a list;
    turning that into a hard `ValueError` is `validate_ensemble_design`'s job
    (an ensemble over zero sources is unrepresentable), not this function's,
    which never raises. A non-dict entry degrades to an empty record rather
    than being skipped, so every entry still contributes a (fallback-numbered)
    row instead of silently vanishing from the list.
    """
    experiments = state.get("experiments")
    if not isinstance(experiments, list):
        return []
    return [
        {
            "experiment_id": _experiment_id(entry if isinstance(entry, dict) else {}, index),
            "oof_path": _oof_path_for_experiment(
                entry if isinstance(entry, dict) else {}, index, workspace
            ),
        }
        for index, entry in enumerate(experiments)
    ]


def _render_base_experiments(base_experiments: list[dict[str, str]]) -> str:
    """Pretty-printed JSON for the `## Base experiments` prompt section, or a
    placeholder when there are none — same "empty degrades to a placeholder
    string" convention as `read_fold_summary`/`read_solution_plan`."""
    if not base_experiments:
        return _NO_BASE_EXPERIMENTS
    return json.dumps(base_experiments, indent=2)


class EnsembleSpecialistNode(LLMNode):
    name = "ensemble_specialist"

    # Resolved in `_build_messages` and consumed in `_write_output`:
    # `LLMNode.__call__` never passes `state` to `_write_output`, so both the
    # workspace-relative feature-spec pointer and the node-injected
    # `base_experiments` list have to be stashed between the two (same
    # mechanism as `timeseries_specialist`/`classical_ml_specialist`).
    # `_base_experiments` defaults to `None` — distinct from the legitimate
    # empty list `_build_base_experiments` returns for an empty
    # `state["experiments"]` — so `_write_output` can tell "never resolved"
    # apart from "resolved to zero experiments".
    _feature_spec_ref: str = ""
    _base_experiments: list[dict[str, str]] | None = None

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        self._feature_spec_ref = resolve_feature_spec_ref(state, workspace)
        self._base_experiments = _build_base_experiments(state, workspace)
        solution_plan = read_solution_plan(state, workspace)
        fold_summary = read_fold_summary(state, workspace)
        messages.append(
            HumanMessage(
                content=(
                    f"## Solution plan\n\n{solution_plan}\n\n"
                    f"## Frozen CV folds\n\n{fold_summary}\n\n"
                    f"## Feature spec reference\n\n{self._feature_spec_ref}\n\n"
                    "## Base experiments (out-of-fold predictions)\n\n"
                    f"{_render_base_experiments(self._base_experiments)}"
                )
            )
        )
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        if not self._feature_spec_ref or self._base_experiments is None:
            raise ValueError(
                f"{_SPECIALIST}._write_output was called before _build_messages resolved the "
                "feature spec reference and base experiments; refusing to write a design with "
                "unknown inputs"
            )
        content = response.content if isinstance(response.content, str) else str(response.content)
        data = extract_json_object(content, _SPECIALIST)
        validated = validate_ensemble_design(
            data,
            specialist=_SPECIALIST,
            allowed_families=_MODEL_FAMILIES,
            feature_spec_ref=self._feature_spec_ref,
            base_experiments=self._base_experiments,
        )
        return workspace.write_json(relative_path, validated)
