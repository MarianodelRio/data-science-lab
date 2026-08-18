"""experiment_designer: turns `hypothesis_generator`'s hypotheses into an
ordered plan for the next iteration, written to
`reports/experiment_plan_{current_iteration}.json`, and **increments
`state["current_iteration"]`**.

Last node of `config/phases/phase6_evaluation.yaml`'s sequence,
`model_role: reasoning`, no critic in this phase.

## The only `current_iteration` writer anywhere in `src/`

Before T-032 nothing in `src/` ever wrote `current_iteration`: `src/state.py`
initializes it to `0` and every node only reads it. The consequence was that
every `{iteration}`-suffixed artifact — `design/iteration_{N}/solution_plan.json`,
`feature_spec.json`, `experiments/exp_{N}/design.json`,
`reports/score_evaluation_{N}.json` — overwrote its predecessor forever, and
the already-landed `ensemble_specialist` could not run at all: its
duplicate-`oof_path` invariant raises when two base experiments both resolve to
`experiments/exp_0/oof_predictions.parquet`. `_build_output_state` here is the
fix, and it is the single place the increment happens.

**Why the artifact still files under the pre-increment number.**
`LLMNode.__call__` calls `_resolve_output_path` (which reads
`state["current_iteration"]`) *before* `_build_output_state`. So this node's own
plan, and all four earlier Phase 6 artifacts, stay aligned with the `exp_{N}`
directory that was just scored. The plan describes the iteration just evaluated
and points forward via its `next_iteration` field.

**Why this node must stay last in the phase sequence.** Any node ordered after
it would resolve its own `{iteration}` against the incremented value and file
its artifact one number ahead of the experiment it describes. The ordering is
pinned by `tests/unit/graph/test_phase_yaml_contracts.py`'s `EXPECTED` tuple for
`phase6_evaluation`.

**Why this is safe against CLAUDE.md invariant #4** (baseline runs only at
`current_iteration == 0`): `src/graph/supervisor.py` routes to `phase3_baseline`
only from `phase2_research` at `current_iteration == 0`. The Phase 6 loop-back
goes to `phase4_design`, which the increment never turns into a baseline re-run.

**Why this is safe against concurrent writes:** `current_iteration` is a plain
`LastValue` channel (no reducer) and would raise `InvalidUpdateError` if two
nodes wrote it in one super-step — Phase 6 declares `parallel_nodes: []` and is
strictly sequential, and this is the field's only writer.

## Degradation

Both inputs (`reports/hypotheses_{N}.json`, `reports/error_diagnosis_{N}.json`)
degrade to explicit placeholders and never raise — see
`_evaluation_llm_common`'s module docstring for the filename-number divergence
that makes that necessary. `changes[].hypothesis_id` is deliberately **not**
cross-validated against the hypotheses file: that file may itself have degraded,
and rejecting the plan on that basis would abort a phase with no critic to retry
it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm import _evaluation_llm_common as common
from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

NODE_NAME = "experiment_designer"

# Where a planned change lands in the next iteration's Phase 4/5 artifacts.
CHANGE_TARGETS: tuple[str, ...] = ("solution_plan", "feature_spec", "experiment_design", "data")

_HYPOTHESES_MISSING = "(hypotheses not yet available)"
_DIAGNOSIS_MISSING = "(error diagnosis not yet available)"

_MAX_CHANGES = 6


def _validate_change(entry: dict[str, Any], index: int) -> dict[str, Any]:
    field = f"changes[{index}]"
    return {
        "order": common.validate_int(entry.get("order"), f"{field}.order", NODE_NAME),
        "change": common.validate_non_empty_str(entry.get("change"), f"{field}.change", NODE_NAME),
        "target": common.validate_enum(
            entry.get("target"), f"{field}.target", CHANGE_TARGETS, NODE_NAME
        ),
        "hypothesis_id": common.validate_non_empty_str(
            entry.get("hypothesis_id"), f"{field}.hypothesis_id", NODE_NAME
        ),
        "expected_effect": common.validate_non_empty_str(
            entry.get("expected_effect"), f"{field}.expected_effect", NODE_NAME
        ),
    }


def _validate_experiment_plan(data: dict[str, Any]) -> dict[str, Any]:
    """Whitelist rebuild of the plan, with `changes` sorted ascending by `order`.

    Sorting here rather than trusting the response order is what makes "an
    ordered list of changes" an assertable property of the artifact.
    """
    raw = common.validate_object_list(
        data.get("changes"), "changes", NODE_NAME, min_len=1, max_len=_MAX_CHANGES
    )
    changes = [_validate_change(entry, i) for i, entry in enumerate(raw)]
    common.validate_rank_permutation([c["order"] for c in changes], "order", NODE_NAME)

    return {
        "changes": sorted(changes, key=lambda c: c["order"]),
        "rationale": common.validate_non_empty_str(data.get("rationale"), "rationale", NODE_NAME),
    }


class ExperimentDesignerNode(LLMNode):
    name = NODE_NAME

    def __init__(
        self,
        *,
        agent_config_dir: str | Path | None = None,
        prompts_dir: str | Path | None = None,
    ) -> None:
        """`_build_messages` stashes the resolved (pre-increment) iteration for
        `_write_output`, which receives no state. Initialized here as well so a
        direct `_write_output` call can never hit an `AttributeError`."""
        super().__init__(agent_config_dir=agent_config_dir, prompts_dir=prompts_dir)
        self._iteration: int = 0

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        iteration = common.current_iteration(state)

        hypotheses = common.read_workspace_json(
            common.HYPOTHESES_PATTERN.format(iteration=iteration), workspace
        )
        diagnosis = common.read_workspace_json(
            common.ERROR_DIAGNOSIS_PATTERN.format(iteration=iteration), workspace
        )

        self._iteration = iteration

        messages.append(
            HumanMessage(
                content=(
                    "## Hypotheses\n\n"
                    f"{common.render_json_section(hypotheses, _HYPOTHESES_MISSING)}\n\n"
                    "## Error diagnosis\n\n"
                    f"{common.render_json_section(diagnosis, _DIAGNOSIS_MISSING)}"
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
        validated = _validate_experiment_plan(common.extract_json_object(content, NODE_NAME))
        artifact = {
            "iteration": self._iteration,
            "next_iteration": self._iteration + 1,
            **validated,
        }
        return workspace.write_json(relative_path, artifact)

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        """The pipeline's single `current_iteration` write — see the module
        docstring for why it lives here, why it is safe, and why this node must
        stay last in the phase sequence."""
        return {"current_iteration": common.current_iteration(state) + 1}
