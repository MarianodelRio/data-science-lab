"""problem_framer: classifies the ML problem type and success metric from the
EDA report and writes reports/problem_definition.json.

Overrides `_build_messages` (inject the EDA report as an extra HumanMessage),
`_write_output` (extract + validate the JSON payload, write it via
`workspace.write_json`), and `_build_output_state` (set
`state["problem_definition_path"]`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _relative_to_workspace(path: str, workspace: WorkspaceManager) -> str:
    """`WorkspaceManager.write_text`/`write_json` return an *absolute* path
    (design.md's WorkspaceManager API table: `write_json(...) -> ...  #
    returns abs path`), and `LLMNode._build_output_state` implementations
    store that return value verbatim into `LabState` path fields (e.g.
    `eda_report_path`). But `read_text`/`read_json` require a *relative*
    path and reject absolute ones. Nodes that consume an upstream node's
    path field (like this one reading `state['eda_report_path']`) must
    therefore re-relativize it against the current workspace root before
    reading — already-relative input (e.g. in unit tests) passes through
    unchanged.
    """
    p = Path(path)
    if not p.is_absolute():
        return path
    return str(p.relative_to(workspace.workspace_path))


def _extract_json(content: str) -> dict[str, Any]:
    """Extract a JSON object from the LLM response.

    Accepts: raw JSON with no fence, a single ```json fenced block, or a
    single unlabeled ``` fenced block. Rejects multiple fenced blocks and
    invalid JSON with a clear ValueError naming 'problem_framer'.
    """
    matches = _JSON_FENCE_RE.findall(content)
    if len(matches) > 1:
        raise ValueError(
            f"problem_framer response contains {len(matches)} fenced code blocks, "
            "expected at most one"
        )
    text = matches[0].strip() if matches else content.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"problem_framer response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"problem_framer response must be a JSON object, got {type(data).__name__}"
        )
    return data


def _validate_problem_definition(data: dict[str, Any]) -> dict[str, Any]:
    problem_type = data.get("problem_type")
    if not isinstance(problem_type, str) or not problem_type:
        raise ValueError(
            "problem_framer response missing required non-empty string field 'problem_type'"
        )

    success_metric = data.get("success_metric")
    if not isinstance(success_metric, str) or not success_metric:
        raise ValueError(
            "problem_framer response missing required non-empty string field 'success_metric'"
        )

    constraints = data.get("constraints", [])
    if not isinstance(constraints, list) or not all(isinstance(c, str) for c in constraints):
        raise ValueError(
            "problem_framer response field 'constraints' must be a list of strings, "
            f"got {constraints!r}"
        )

    return {
        "problem_type": problem_type,
        "success_metric": success_metric,
        "constraints": constraints,
    }


class ProblemFramerNode(LLMNode):
    name = "problem_framer"

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        eda_report = workspace.read_text(
            _relative_to_workspace(state["eda_report_path"], workspace)
        )
        messages.append(HumanMessage(content=f"## EDA report\n\n{eda_report}"))
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        data = _extract_json(content)
        validated = _validate_problem_definition(data)
        return workspace.write_json(relative_path, validated)

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        return {"problem_definition_path": written_path}
