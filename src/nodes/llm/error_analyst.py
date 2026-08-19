"""error_analyst: diagnoses the root cause of the iteration that just finished
and writes `reports/error_diagnosis_{current_iteration}.json`.

Third node of `config/phases/phase6_evaluation.yaml`'s sequence
(`score_evaluator -> feature_importance_extractor -> error_analyst ->
hypothesis_generator -> experiment_designer`), `model_role: reasoning`, no
critic in this phase.

## Inputs, and how the experiment directory is obtained

Four workspace artifacts, all read through
`_evaluation_llm_common.read_workspace_json`:

1. `reports/score_evaluation_{iteration}.json` — written unconditionally by
   `score_evaluator`, including on its "nothing to evaluate" path.
2. `reports/feature_importance_{iteration}.json` — written unconditionally by
   `feature_importance_extractor`, including on its skip path.
3. `results.json` and 4. `design.json`, both joined onto the **score artifact's
   own `experiment_dir` field** via `join_experiment_file`.

The experiment directory is **never re-derived here**. `state["experiments"]`
is deliberately not read (its pointer can name the first rather than the last
regenerated experiment), and neither `resolve_output_iteration` nor
`candidate_experiment_dirs` is imported from `src/nodes/compute/` or
reimplemented — see `_evaluation_llm_common`'s module docstring for the full
reasoning. Score polarity is likewise read (`direction`) rather than
re-derived: `LabState` carries no polarity field, and `score_evaluation_{N}.json`
records the direction per call.

## Degradation, and the filename-number fragility

`score_evaluator` names its report from
`_evaluation_common.resolve_output_iteration` (derived from the `exp_{N}`
directory it actually read) while this node reads at
`state["current_iteration"]`. Those can diverge. Every read therefore degrades
to an explicit placeholder string and this node never raises on a missing or
unreadable input — the artifact's `inputs` block records which of the four were
actually read, so a degraded diagnosis is detectable after the fact.

## No leaderboard data

`cv_lb_divergence` is retained as design.md's vocabulary, but no leaderboard
score exists anywhere in this pipeline (`LabState` has no such field and the
`kaggle_client` node is unbuilt). The prompt states this explicitly and forbids
inventing one; diagnosis inputs are the CV score, the baseline score and the
feature importance report only.

## No `LabState` field

`_build_output_state` is deliberately **not** overridden — this node returns
`{}` beyond `messages`. `src/state.py` is a protected contract and adding a
field for this artifact would require human approval; the real consumer is the
next iteration's Phase 4, reading the workspace file (plus the appended
`AIMessage` already in `messages`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm import _evaluation_llm_common as common
from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

NODE_NAME = "error_analyst"

# The `inputs` block's keys, in written order. Each maps to the workspace-relative
# path actually read, or `None` when that artifact was missing or unreadable.
INPUT_KEYS = ("score_evaluation", "feature_importance", "experiment_results", "experiment_design")

_SCORE_MISSING = "(score evaluation not yet available)"
_FEATURE_IMPORTANCE_MISSING = "(feature importance report not yet available)"
_RESULTS_MISSING = "(experiment results not available)"
_DESIGN_MISSING = "(experiment design not available)"

_MAX_EVIDENCE_ENTRIES = 8


def _validate_diagnosis(data: dict[str, Any]) -> dict[str, Any]:
    """Whitelist rebuild of the LLM's diagnosis — a fresh dict with exactly the
    four pinned keys, so a stray key the model invents never reaches the
    artifact. Every failure raises `ValueError` naming `error_analyst` and the
    offending field."""
    return {
        "root_cause": common.validate_enum(
            data.get("root_cause"), "root_cause", common.ROOT_CAUSES, NODE_NAME
        ),
        "confidence": common.validate_unit_interval(
            data.get("confidence"), "confidence", NODE_NAME
        ),
        "evidence": common.validate_str_list(
            data.get("evidence"), "evidence", NODE_NAME, min_len=1, max_len=_MAX_EVIDENCE_ENTRIES
        ),
        "recommended_focus": common.validate_non_empty_str(
            data.get("recommended_focus"), "recommended_focus", NODE_NAME
        ),
    }


class ErrorAnalystNode(LLMNode):
    name = NODE_NAME

    def __init__(
        self,
        *,
        agent_config_dir: str | Path | None = None,
        prompts_dir: str | Path | None = None,
    ) -> None:
        """`LLMNode.__call__` runs `_build_messages` before `_write_output` in
        the same call, and Phase 6 is strictly sequential
        (`parallel_nodes: []`), so `_build_messages` stashes the resolved
        iteration and the per-input paths for `_write_output` to inject into the
        artifact. They are initialized here — not only in `_build_messages` — so
        a direct `_write_output` call can never hit an `AttributeError`."""
        super().__init__(agent_config_dir=agent_config_dir, prompts_dir=prompts_dir)
        self._iteration: int = 0
        self._input_paths: dict[str, str | None] = dict.fromkeys(INPUT_KEYS)

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        iteration = common.current_iteration(state)

        score_path = common.SCORE_EVALUATION_PATTERN.format(iteration=iteration)
        score = common.read_workspace_json(score_path, workspace)
        importance_path = common.FEATURE_IMPORTANCE_PATTERN.format(iteration=iteration)
        importance = common.read_workspace_json(importance_path, workspace)

        experiment_dir = score.get("experiment_dir") if score else None
        results_path = common.join_experiment_file(experiment_dir, common.RESULTS_FILENAME)
        results = common.read_workspace_json(results_path, workspace)
        design_path = common.join_experiment_file(experiment_dir, common.DESIGN_FILENAME)
        design = common.read_workspace_json(design_path, workspace)

        self._iteration = iteration
        self._input_paths = {
            "score_evaluation": score_path if score is not None else None,
            "feature_importance": importance_path if importance is not None else None,
            "experiment_results": results_path if results is not None else None,
            "experiment_design": design_path if design is not None else None,
        }

        messages.append(
            HumanMessage(
                content=(
                    "## Score evaluation\n\n"
                    f"{common.render_json_section(score, _SCORE_MISSING)}\n\n"
                    "## Feature importance\n\n"
                    f"{common.render_json_section(importance, _FEATURE_IMPORTANCE_MISSING)}\n\n"
                    "## Experiment results\n\n"
                    f"{common.render_json_section(results, _RESULTS_MISSING)}\n\n"
                    "## Experiment design\n\n"
                    f"{common.render_json_section(design, _DESIGN_MISSING)}"
                )
            )
        )
        return messages

    def _resolve_output_path(self, state: LabState) -> str:
        """Same coerced iteration the artifact's own `iteration` field records,
        so the filename number and the recorded number can never disagree.
        `LLMNode`'s default reads `state["current_iteration"]` raw, which would
        file a boolean as `..._True.json` next to an `"iteration": 0` body."""
        return self.config.output_file_pattern.format(iteration=common.current_iteration(state))

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        validated = _validate_diagnosis(common.extract_json_object(content, NODE_NAME))
        artifact = {"iteration": self._iteration, **validated, "inputs": dict(self._input_paths)}
        return workspace.write_json(relative_path, artifact)
