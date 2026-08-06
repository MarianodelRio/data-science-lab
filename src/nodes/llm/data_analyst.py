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
    match = _CODE_FENCE_RE.search(content)
    if match is None:
        raise ValueError(
            "data_analyst response contains no fenced ```python code block "
            "(expected exactly one per config/prompts/data_analyst/v1.md)"
        )
    code = match.group(1).strip()
    narrative = _CODE_FENCE_RE.sub("", content, count=1).strip()
    return code, narrative


def _build_report_markdown(narrative: str, result: ExecResult) -> str:
    lines = [
        "# EDA Report",
        "",
        narrative,
        "",
        "## Execution output",
        "",
        "```",
        result.stdout.strip(),
        "```",
    ]
    if result.returncode != 0 or result.timed_out:
        lines += [
            "",
            "## Execution errors",
            "",
            f"- returncode: {result.returncode}",
            f"- timed_out: {result.timed_out}",
            "",
            "```",
            result.stderr.strip(),
            "```",
        ]
    return "\n".join(lines) + "\n"


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

        workspace.write_notebook(
            "notebooks/01_eda.ipynb",
            cells=[
                {"cell_type": "markdown", "source": narrative},
                {"cell_type": "code", "source": code},
            ],
        )
        return written_path

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        return {"eda_report_path": written_path}
