"""deep_learning_specialist: reads the solution plan and the frozen fold summary
and designs one neural experiment on tabular data — model family (TabNet, NODE,
or an MLP with learned categorical embeddings), Optuna search space, fixed
params, model-specific preprocessing — writing
experiments/exp_{iteration}/design.json.

The `design.json` schema itself lives in `src/nodes/llm/_experiment_design.py`,
shared with the other Pipeline Phase 5 specialists and their `coder` consumer,
and is **not** modified by this node: `normalize_model_family` takes the family
table as a parameter, so a new specialist contributes its own table rather than
extending a shared one.

Two neural-specific requirements live in `config/prompts/deep_learning_specialist/v1.md`
rather than in the validator, because neither is expressible in the current
schema: preprocessing steps that need fitting must be fitted inside each fold
(`preprocessing` is a flat token list with no fit-scope notion), and an
architecture must be described through scalar parameters (`choices` accepts only
JSON scalars, so a list of layer-width tuples is rejected). See
`context/discoveries.md` for the fit-scope hand-off to `coder` (T-029).

The task's "activated only when the dataset is large enough" condition is
likewise prompt-level: `LabState` carries no row count or shape, and by the time
this node runs `specialist_selector` has already routed the iteration here, so
the prompt degrades capacity instead of refusing.

Overrides `_build_messages` (inject the solution plan, the frozen fold summary
and the feature-spec reference as an extra HumanMessage) and `_write_output`
(extract + validate the JSON payload, write it via `workspace.write_json`).
Does NOT override `_build_output_state` — `coder` (T-029) reads
`experiments/exp_{iteration}/design.json` from its well-known path directly, the
same convention `classical_ml_specialist` already uses, so no new `LabState`
field is needed (`src/state.py` is a protected contract).
"""

from __future__ import annotations

import json

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm._experiment_design import (
    DEGRADE_ERRORS,
    extract_json_object,
    read_fold_summary,
    resolve_feature_spec_ref,
    validate_experiment_design,
)
from src.nodes.llm.base import LLMNode, relative_to_workspace
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

_SPECIALIST = "deep_learning_specialist"

# Canonical model family -> phrases that denote it. Matched as whole phrases with
# word boundaries against a separator-normalized copy of the LLM's `model_family`
# string (see `_experiment_design.normalize_model_family`), so `TabNet`/`tab-net`
# and `Multi-Layer Perceptron`/`multilayer perceptron` all resolve to the
# canonical key `coder` (T-029) dispatches on.
#
# `node` (Neural Oblivious Decision Ensembles) is a deliberately short canonical
# token, kept for literature fidelity and symmetry with the other two. Word-
# boundary matching makes it safe: `nodes`, `NODEv2` and `node_count` do not
# match it. Its spelled-out aliases belong to the same family, so
# `"NODE (Neural Oblivious Decision Ensembles)"` resolves to one family rather
# than being flagged ambiguous — while a genuinely two-family answer like
# `"MLP node"` is still rejected, which is the intended outcome.
_MODEL_FAMILIES: dict[str, tuple[str, ...]] = {
    "tabnet": ("tabnet", "tab net"),
    "node": (
        "node",
        "neural oblivious decision ensembles",
        "neural oblivious decision ensemble",
    ),
    "mlp": (
        "mlp",
        "multilayer perceptron",
        "multi layer perceptron",
        "feedforward network",
        "feed forward network",
    ),
}


def _read_solution_plan(state: LabState, workspace: WorkspaceManager) -> str:
    """Read state['solution_plan_path'] as pretty-printed JSON text. Degrades to a
    placeholder, never raises — own copy of
    `classical_ml_specialist._read_solution_plan`, per the established
    per-module-duplication convention for these upstream-artifact readers
    (T-020/T-022/T-024 decision-log entries).

    This is the third copy carrying the wider `DEGRADE_ERRORS` catch. Hoisting it
    would mean editing `_experiment_design.py`, which is frozen for this task
    (it is the contract T-026–T-028 inherit and T-029 consumes), and importing it
    from a sibling node module is the cross-module dependency T-024 explicitly
    discarded — see `context/discoveries.md`.
    """
    path = state.get("solution_plan_path") or ""
    if not isinstance(path, str) or not path:
        return "(solution plan not yet available)"
    try:
        data = workspace.read_json(relative_to_workspace(path, workspace))
        return json.dumps(data, indent=2)
    except DEGRADE_ERRORS:
        return f"(unable to read solution plan at {path})"


class DeepLearningSpecialistNode(LLMNode):
    name = "deep_learning_specialist"

    # Resolved in `_build_messages` and consumed in `_write_output`:
    # `LLMNode.__call__` never passes `state` to `_write_output`, so the
    # workspace-relative feature-spec pointer has to be stashed between the two
    # (same mechanism as `classical_ml_specialist`).
    _feature_spec_ref: str = ""

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        self._feature_spec_ref = resolve_feature_spec_ref(state, workspace)
        solution_plan = _read_solution_plan(state, workspace)
        fold_summary = read_fold_summary(state, workspace)
        messages.append(
            HumanMessage(
                content=(
                    f"## Solution plan\n\n{solution_plan}\n\n"
                    f"## Frozen CV folds\n\n{fold_summary}\n\n"
                    f"## Feature spec reference\n\n{self._feature_spec_ref}"
                )
            )
        )
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        if not self._feature_spec_ref:
            raise ValueError(
                f"{_SPECIALIST}._write_output was called before _build_messages resolved the "
                "feature spec reference; refusing to write a design with an unknown "
                "'feature_spec_ref'"
            )
        content = response.content if isinstance(response.content, str) else str(response.content)
        data = extract_json_object(content, _SPECIALIST)
        validated = validate_experiment_design(
            data,
            specialist=_SPECIALIST,
            allowed_families=_MODEL_FAMILIES,
            feature_spec_ref=self._feature_spec_ref,
        )
        return workspace.write_json(relative_path, validated)
