"""validation_strategist: selects a CV strategy and freezes fold indices.

Overrides `_build_messages` (inject the problem definition + EDA report as an
extra HumanMessage), `_write_output` (parse the LLM's fenced code block, run it
through `code_executor.execute` — never inline `exec`/`eval` — parse its single
JSON stdout line, and write `validation/fold_config.json` exactly once), and
`_build_output_state` (set `state["validation_config_path"]`).

Enforces CLAUDE.md invariant #1: `validation/fold_config.json` is write-once,
frozen after Pipeline Phase 1. `_write_output` checks for an existing file
*before* doing anything else — before invoking `execute` again — and raises
`FoldsAlreadyFrozenError` rather than recomputing/overwriting it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm.base import LLMNode
from src.nodes.llm.errors import FoldsAlreadyFrozenError
from src.state import LabState
from src.tools.code_executor import execute
from src.workspace.workspace_manager import WorkspaceManager

_CODE_FENCE_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
_REQUIRED_KEYS = ("strategy", "n_folds", "fold_indices", "seed")


def _extract_code(content: str) -> str:
    """Extract the single fenced ```python code block. Expects exactly one
    fenced block per the v1 prompt's output-format instructions."""
    matches = _CODE_FENCE_RE.findall(content)
    if len(matches) == 0:
        raise ValueError(
            "validation_strategist response contains no fenced ```python code block "
            "(expected exactly one per config/prompts/validation_strategist/v1.md)"
        )
    if len(matches) > 1:
        raise ValueError(
            f"validation_strategist response contains {len(matches)} fenced ```python "
            "code blocks, expected exactly one"
        )
    return matches[0].strip()


def _read_upstream_context(state: LabState) -> str:
    """Build a HumanMessage body from the problem definition and EDA report.

    Uses plain `pathlib.Path` (NOT `WorkspaceManager`) because these `LabState`
    path fields already hold absolute, previously-validated paths written by
    upstream nodes (`problem_framer`, `data_analyst`) via `WorkspaceManager` —
    reading them back does not need to go through the workspace's
    relative-path/traversal guard again.
    """
    blocks: list[str] = []
    for label, key in (
        ("Problem definition", "problem_definition_path"),
        ("EDA report", "eda_report_path"),
    ):
        # `key` is a plain `str`, not a `Literal[...]`, so `TypedDict.get` can't
        # narrow the return type past `object` — coerce explicitly for mypy;
        # every LabState path field is a `str` at runtime regardless.
        path_str = str(state.get(key) or "")
        if not path_str:
            content = f"({label} not yet available)"
        else:
            try:
                content = Path(path_str).read_text(encoding="utf-8")
            except OSError:
                content = f"(unable to read {label} at {path_str})"
        blocks.append(f"## {label}\n\n{content}")
    return "\n\n".join(blocks)


class ValidationStrategistNode(LLMNode):
    name = "validation_strategist"

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        base = super()._build_messages(trimmed_messages, state)
        return [*base, HumanMessage(content=_read_upstream_context(state))]

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        target_path = Path(workspace.workspace_path) / relative_path
        if target_path.exists():
            raise FoldsAlreadyFrozenError(
                f"{relative_path} already exists; validation folds are frozen after "
                "Pipeline Phase 1 and must not be recomputed."
            )

        content = response.content if isinstance(response.content, str) else str(response.content)
        code = _extract_code(content)

        result = execute(code, cwd=str(workspace.workspace_path))
        if result.returncode != 0 or result.timed_out:
            raise ValueError(
                f"validation_strategist code execution failed "
                f"(returncode={result.returncode}, timed_out={result.timed_out}): {result.stderr}"
            )

        stdout = result.stdout.strip()
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"validation_strategist code did not print a single JSON object to "
                f"stdout: {exc}; stdout={stdout!r}"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                f"validation_strategist stdout JSON must be an object, got {type(payload).__name__}"
            )

        missing = [key for key in _REQUIRED_KEYS if key not in payload]
        if missing:
            raise ValueError(f"validation_strategist stdout JSON missing required keys: {missing}")

        fold_config = {key: payload[key] for key in _REQUIRED_KEYS}
        return workspace.write_json(relative_path, fold_config)

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        return {"validation_config_path": written_path}
