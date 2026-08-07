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


def _validate_fold_config_shape(payload: dict[str, Any]) -> None:
    """Validate the *values* of the required keys, not just their presence.

    `validation/fold_config.json` is write-once (`FoldsAlreadyFrozenError`
    guarantees it can never be recomputed), so a structurally-valid-but-garbage
    payload — e.g. an empty `fold_indices`, a fold missing `train`/`val`, or an
    `n_folds` that doesn't match the number of folds actually produced — would
    otherwise get permanently frozen with no recovery path. Raises `ValueError`
    for every shape violation, mirroring the other validation failures in
    `_write_output`. `bool` is deliberately excluded from the int checks even
    though `isinstance(True, int)` is `True` in Python — a JSON `true`/`false`
    is never a valid `n_folds`/`seed`.
    """
    n_folds = payload["n_folds"]
    if not isinstance(n_folds, int) or isinstance(n_folds, bool) or n_folds <= 0:
        raise ValueError(
            f"validation_strategist stdout JSON 'n_folds' must be a positive int, got {n_folds!r}"
        )

    seed = payload["seed"]
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError(f"validation_strategist stdout JSON 'seed' must be an int, got {seed!r}")

    fold_indices = payload["fold_indices"]
    if not isinstance(fold_indices, list) or len(fold_indices) == 0:
        raise ValueError(
            f"validation_strategist stdout JSON 'fold_indices' must be a non-empty list, "
            f"got {fold_indices!r}"
        )

    for i, fold in enumerate(fold_indices):
        if (
            not isinstance(fold, dict)
            or "train" not in fold
            or "val" not in fold
            or not isinstance(fold["train"], list)
            or not isinstance(fold["val"], list)
        ):
            raise ValueError(
                f"validation_strategist stdout JSON 'fold_indices[{i}]' must be a dict with "
                f"'train' and 'val' list keys, got {fold!r}"
            )

    if len(fold_indices) != n_folds:
        raise ValueError(
            f"validation_strategist stdout JSON 'n_folds' ({n_folds}) does not match "
            f"len(fold_indices) ({len(fold_indices)})"
        )


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

        _validate_fold_config_shape(payload)

        fold_config = {key: payload[key] for key in _REQUIRED_KEYS}
        return workspace.write_json(relative_path, fold_config)

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        return {"validation_config_path": written_path}
