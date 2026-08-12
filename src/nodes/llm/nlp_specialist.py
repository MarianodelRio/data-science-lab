"""nlp_specialist: reads the solution plan and the frozen fold summary and
designs one text/NLP experiment — model family, Optuna search space, fixed
params, model-specific preprocessing — writing
experiments/exp_{iteration}/design.json.

The `design.json` schema itself lives in `src/nodes/llm/_experiment_design.py`,
shared with the other Pipeline Phase 5 specialists and their `coder` consumer.

Overrides `_build_messages` (inject the solution plan, the frozen fold summary
and the feature-spec reference as an extra HumanMessage) and `_write_output`
(extract + validate the JSON payload, write it via `workspace.write_json`).
Does NOT override `_build_output_state` — `coder` (T-029) reads
`experiments/exp_{iteration}/design.json` from its well-known path directly, the
same convention `baseline_designer`/`fold_config.json` already use, so no new
`LabState` field is needed (`src/state.py` is a protected contract).

This node never self-gates on whether text features exist: `specialist_selector`
is the only gate, and by the time this node runs the routing decision has
already been made.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm._experiment_design import (
    extract_json_object,
    read_fold_summary,
    read_solution_plan,
    resolve_feature_spec_ref,
    validate_experiment_design,
)
from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

_SPECIALIST = "nlp_specialist"

# Canonical model family -> phrases that denote it. Matched as whole phrases with
# word boundaries against a separator-normalized copy of the LLM's `model_family`
# string (see `_experiment_design.normalize_model_family`), so `TF-IDF`, `SBERT`,
# `DistilBERT fine-tuning` all resolve to the canonical key `coder` (T-029)
# dispatches on. Human checkpoint decision: exactly three canonical families.
_MODEL_FAMILIES: dict[str, tuple[str, ...]] = {
    "tfidf_linear": ("tfidf linear", "tfidf", "tf idf", "bag of words", "bow"),
    "sentence_embeddings": (
        "sentence embeddings",
        "sentence transformer",
        "sentence transformers",
        "sbert",
        "frozen embeddings",
    ),
    "transformer_finetune": (
        "transformer finetune",
        "transformer fine tune",
        "transformer fine tuning",
        "fine tune transformer",
        "bert finetune",
        "bert fine tuning",
        "distilbert finetune",
        "distilbert fine tuning",
    ),
}


class NlpSpecialistNode(LLMNode):
    name = "nlp_specialist"

    # Resolved in `_build_messages` and consumed in `_write_output`:
    # `LLMNode.__call__` never passes `state` to `_write_output`, so the
    # workspace-relative feature-spec pointer has to be stashed between the two
    # (same mechanism as `classical_ml_specialist`/`literature_researcher`).
    _feature_spec_ref: str = ""

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        self._feature_spec_ref = resolve_feature_spec_ref(state, workspace)
        solution_plan = read_solution_plan(state, workspace)
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
