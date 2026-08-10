"""leakage_auditor: audits the EDA report + problem definition for data
leakage risks and writes reports/leakage_audit.json.

Overrides `_build_messages` (inject the EDA report and problem definition as
an extra HumanMessage) and `_write_output` (extract + validate the JSON
payload, write it via `workspace.write_json`). Does NOT override
`_build_output_state` — there is no `LabState` field for this output, so its
delta stays the base-class default of `{"messages": [...]}` only.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm.base import LLMNode, relative_to_workspace
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager


def _strip_outer_fence(content: str) -> str:
    """Strip a single outer fence wrapping the entire response, if present.

    The v1 prompt requires the response to be EITHER raw JSON OR the entire
    response wrapped in exactly one fence (no prose before/after) — so this
    anchors on the first and last ``` markers rather than counting fence
    occurrences via regex. A `findall`-based non-greedy regex
    (``` ```(?:json)?\\s*\\n(.*?)``` ```) would stop at the FIRST embedded ```
    it finds, which truncates the JSON mid-string whenever a string value
    inside the JSON itself contains a literal ``` sequence (e.g. a
    leakage-finding description quoting a code snippet) — a real defect the
    adversarial reviewer reproduced live. Anchoring on the outermost markers
    instead means embedded ``` runs inside the JSON body are just content,
    never mistaken for the closing fence.
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    if not text.endswith("```") or len(text) < 6:
        raise ValueError("leakage_auditor response starts with a fence but never closes it")
    first_newline = text.find("\n")
    if first_newline == -1:
        raise ValueError("leakage_auditor response fence has no content")
    inner = text[first_newline + 1 :]
    closing_idx = inner.rfind("```")
    if closing_idx == -1:
        raise ValueError("leakage_auditor response fence has no closing delimiter")
    return inner[:closing_idx].strip()


def _extract_json(content: str) -> dict[str, Any]:
    """Extract a JSON object from the LLM response.

    Accepts: raw JSON with no fence, or the entire response wrapped in a
    single ```json or unlabeled ``` fence. Invalid JSON raises a clear
    ValueError naming 'leakage_auditor'.
    """
    text = _strip_outer_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"leakage_auditor response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"leakage_auditor response must be a JSON object, got {type(data).__name__}"
        )
    return data


def _validate_leakage_audit(data: dict[str, Any]) -> dict[str, Any]:
    leaks = data.get("leaks")
    if not isinstance(leaks, list):
        raise ValueError(
            f"leakage_auditor response missing required list field 'leaks', got {leaks!r}"
        )

    severity = data.get("severity")
    if not isinstance(severity, str) or not severity.strip():
        raise ValueError(
            "leakage_auditor response missing required non-empty string field 'severity'"
        )

    blocks_progression = data.get("blocks_progression")
    # bool is a subclass of int in Python, but that's not a concern here since
    # we require `isinstance(x, bool)` strictly — this rejects int/str/None,
    # not just str, matching the "real JSON boolean only" requirement.
    if not isinstance(blocks_progression, bool):
        raise ValueError(
            "leakage_auditor response field 'blocks_progression' must be a JSON boolean "
            f"(true/false), got {blocks_progression!r}"
        )

    return {
        "leaks": leaks,
        "severity": severity,
        "blocks_progression": blocks_progression,
    }


class LeakageAuditorNode(LLMNode):
    name = "leakage_auditor"

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        eda_report = workspace.read_text(relative_to_workspace(state["eda_report_path"], workspace))
        problem_definition = workspace.read_json(
            relative_to_workspace(state["problem_definition_path"], workspace)
        )
        messages.append(
            HumanMessage(
                content=(
                    f"## EDA report\n\n{eda_report}\n\n"
                    "## Problem definition\n\n"
                    f"{json.dumps(problem_definition, indent=2)}"
                )
            )
        )
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        data = _extract_json(content)
        validated = _validate_leakage_audit(data)
        return workspace.write_json(relative_path, validated)
