"""baseline_designer: reads the problem definition + EDA report and designs a
non-trivial, non-tuned baseline experiment, writing experiments/baseline/design.json.

Overrides `_build_messages` (inject the problem definition and EDA report as an
extra HumanMessage), `_write_output` (extract + validate the JSON payload, inject
the fixed `cv_strategy_ref` pointer, write it via `workspace.write_json`). Does NOT
override `_build_output_state` — `baseline_runner` (the next node in Pipeline Phase
3, `src/nodes/compute/baseline_runner.py`) reads `experiments/baseline/design.json`
from its fixed, well-known path directly, the same convention `fold_config.json`
uses, so there is no need for a new `LabState` field (out of scope per T-020 — do
not modify the protected `src/state.py` contract).
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm.base import LLMNode, relative_to_workspace
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

# `validation/fold_config.json` is write-once (CLAUDE.md invariant #1) and always
# lives at this fixed workspace-relative path — never trust the LLM to name it
# correctly, inject it ourselves.
_CV_STRATEGY_REF = "validation/fold_config.json"


def _strip_outer_fence(content: str) -> str:
    """Strip a single outer fence wrapping the entire response, if present.

    Same outer-fence-anchoring approach as `problem_framer._strip_outer_fence`/
    `leakage_auditor._strip_outer_fence`: anchors on the outermost ``` markers
    only, so an embedded ``` inside a string value (e.g. a hyperparameter
    description quoting code) is never mistaken for the closing fence.
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    if not text.endswith("```") or len(text) < 6:
        raise ValueError("baseline_designer response starts with a fence but never closes it")
    first_newline = text.find("\n")
    if first_newline == -1:
        raise ValueError("baseline_designer response fence has no content")
    inner = text[first_newline + 1 :]
    closing_idx = inner.rfind("```")
    if closing_idx == -1:
        raise ValueError("baseline_designer response fence has no closing delimiter")
    return inner[:closing_idx].strip()


def _extract_json(content: str) -> dict[str, Any]:
    """Extract a JSON object from the LLM response.

    Accepts: raw JSON with no fence, or the entire response wrapped in a
    single ```json or unlabeled ``` fence. Invalid JSON raises a clear
    ValueError naming 'baseline_designer'.
    """
    text = _strip_outer_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"baseline_designer response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"baseline_designer response must be a JSON object, got {type(data).__name__}"
        )
    return data


def _validate_features(value: Any) -> str | list[str]:
    if value == "all":
        return "all"
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    raise ValueError(
        "baseline_designer response field 'features' must be the string 'all' or a list of "
        f"strings, got {value!r}"
    )


def _validate_design(data: dict[str, Any]) -> dict[str, Any]:
    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError(
            "baseline_designer response missing required non-empty string field 'model'"
        )

    hyperparameters = data.get("hyperparameters")
    if not isinstance(hyperparameters, dict):
        raise ValueError(
            "baseline_designer response missing required dict field 'hyperparameters', got "
            f"{hyperparameters!r}"
        )

    features = _validate_features(data.get("features"))

    target_column = data.get("target_column")
    if not isinstance(target_column, str) or not target_column.strip():
        raise ValueError(
            "baseline_designer response missing required non-empty string field 'target_column'"
        )

    if isinstance(features, list) and target_column in features:
        # Defense-in-depth against target leakage: baseline_runner's generated
        # training script already excludes target_column from an explicit
        # features list unconditionally, but reject it here too so a leaking
        # design is never written to disk as the LLM's stated intent in the
        # first place.
        raise ValueError(
            f"baseline_designer response 'features' list must not include the target column "
            f"{target_column!r} — got {features!r}"
        )

    return {
        "model": model,
        "hyperparameters": hyperparameters,
        "features": features,
        "target_column": target_column,
        "cv_strategy_ref": _CV_STRATEGY_REF,
    }


def _read_problem_definition(state: LabState, workspace: WorkspaceManager) -> str:
    """Read `state['problem_definition_path']` as pretty-printed JSON text,
    degrading to a placeholder string (never raising) when the path is
    unset or unreadable — mirrors `validation_strategist._read_upstream_context`
    and `analysis_critic._read_target_content`'s handling of a not-yet-available
    upstream artifact (e.g. Pipeline Phase 3 exercised standalone, with no
    Phase 1 run ahead of it, as in the phase-subgraph smoke test)."""
    path = state.get("problem_definition_path") or ""
    if not path:
        return "(problem definition not yet available)"
    try:
        data = workspace.read_json(relative_to_workspace(path, workspace))
    except OSError:
        return f"(unable to read problem definition at {path})"
    return json.dumps(data, indent=2)


def _read_eda_report(state: LabState, workspace: WorkspaceManager) -> str:
    """Same not-yet-available/unreadable degradation as `_read_problem_definition`,
    for `state['eda_report_path']`."""
    path = state.get("eda_report_path") or ""
    if not path:
        return "(EDA report not yet available)"
    try:
        return workspace.read_text(relative_to_workspace(path, workspace))
    except OSError:
        return f"(unable to read EDA report at {path})"


class BaselineDesignerNode(LLMNode):
    name = "baseline_designer"

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        problem_definition = _read_problem_definition(state, workspace)
        eda_report = _read_eda_report(state, workspace)
        messages.append(
            HumanMessage(
                content=(
                    f"## Problem definition\n\n{problem_definition}\n\n"
                    f"## EDA report\n\n{eda_report}"
                )
            )
        )
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        data = _extract_json(content)
        validated = _validate_design(data)
        return workspace.write_json(relative_path, validated)

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        return {}
