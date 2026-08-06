"""data_analyst: runs EDA by generating Python and executing it via code_executor.

Overrides only `_write_output` (parse the LLM's fenced code block, run it through
`code_executor.execute` — never inline `exec`/`eval` — and write both the report
and the notebook) and `_build_output_state` (set `state["eda_report_path"]`).
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.messages import BaseMessage

from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.tools.code_executor import ExecResult, execute
from src.workspace.workspace_manager import WorkspaceManager

_CODE_FENCE_RE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)


def _extract_code_and_narrative(content: str) -> tuple[str, str]:
    """Split the LLM response into (code, narrative). Expects exactly one
    fenced ```python block per the v1 prompt's output-format instructions."""
    matches = _CODE_FENCE_RE.findall(content)
    if len(matches) == 0:
        raise ValueError(
            "data_analyst response contains no fenced ```python code block "
            "(expected exactly one per config/prompts/data_analyst/v1.md)"
        )
    if len(matches) > 1:
        raise ValueError(
            f"data_analyst response contains {len(matches)} fenced ```python "
            "code blocks, expected exactly one"
        )
    code = matches[0].strip()
    narrative = _CODE_FENCE_RE.sub("", content, count=1).strip()
    return code, narrative


def _fence_for(content: str) -> str:
    """Pick a backtick fence delimiter long enough that it can't be
    prematurely closed by a run of backticks already present in `content`.

    Standard Markdown-safe technique: a fence only closes on a line with
    at least as many backticks as it opened with, so using a fence one
    backtick longer than the longest run in the content guarantees the
    content can never terminate it early. No exec/eval involved — this is
    plain markdown text manipulation.
    """
    longest_run = 0
    current_run = 0
    for char in content:
        if char == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    return "`" * max(3, longest_run + 1)


def _build_report_markdown(narrative: str, result: ExecResult) -> str:
    stdout = result.stdout.strip()
    stdout_fence = _fence_for(stdout)
    lines = [
        "# EDA Report",
        "",
        narrative,
        "",
        "## Execution output",
        "",
        stdout_fence,
        stdout,
        stdout_fence,
    ]
    if result.returncode != 0 or result.timed_out:
        stderr = result.stderr.strip()
        stderr_fence = _fence_for(stderr)
        lines += [
            "",
            "## Execution errors",
            "",
            f"- returncode: {result.returncode}",
            f"- timed_out: {result.timed_out}",
            "",
            stderr_fence,
            stderr,
            stderr_fence,
        ]
    return "\n".join(lines) + "\n"


def _failure_note(result: ExecResult) -> str:
    """Build the markdown source for the notebook's failure cell, appended
    only when execution did not succeed, so the notebook is self-describing
    without a human/agent having to cross-reference the report."""
    stderr_excerpt = result.stderr.strip()
    return "\n".join(
        [
            "## Execution failed",
            "",
            f"- returncode: {result.returncode}",
            f"- timed_out: {result.timed_out}",
            "",
            _fence_for(stderr_excerpt),
            stderr_excerpt,
            _fence_for(stderr_excerpt),
        ]
    )


class DataAnalystNode(LLMNode):
    name = "data_analyst"

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        code, narrative = _extract_code_and_narrative(content)

        result = execute(code, cwd=str(workspace.workspace_path))

        report_markdown = _build_report_markdown(narrative, result)
        written_path = workspace.write_text(relative_path, report_markdown)

        cells: list[dict[str, str]] = [
            {"cell_type": "markdown", "source": narrative},
            {"cell_type": "code", "source": code},
        ]
        if result.returncode != 0 or result.timed_out:
            cells.append({"cell_type": "markdown", "source": _failure_note(result)})

        workspace.write_notebook("notebooks/01_eda.ipynb", cells=cells)
        return written_path

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        return {"eda_report_path": written_path}
