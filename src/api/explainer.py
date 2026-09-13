"""Read-only chat explainer agent.

Per `docs/adr/0002-explainer-is-not-an-llmnode.md`, `Explainer` is a plain
Python class — NOT an `src.nodes.llm.base.LLMNode` subclass and NOT a
LangGraph `StateGraph`. It never writes to the workspace and never mutates
`LabState`; forwarding a human's approve/redirect decision at an interrupt is
handled entirely by `src/api/routers/chat.py` via `do_resume`, not by this
class.

`answer()` is synchronous and fully blocking (LLM call, workspace reads,
optional RAG query) — the caller runs it via `asyncio.to_thread`, which is
the single seam that keeps every blocking call in this class off the event
loop. This also keeps the class trivially unit-testable with no asyncio
machinery.

A fresh `Explainer` is built per WebSocket connection by `chat.py` (not
cached on `app.state`): its dependencies are all connection/run-scoped, and
construction is cheap (two file reads, no network call until `.invoke()`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.llm.factory import LLMFactory
from src.workspace.workspace_manager import WorkspaceManager

logger = logging.getLogger(__name__)

# `state_values` fields naming a workspace-relative report the explainer may
# surface as context, in the order they should appear when present.
_CONTEXT_PATH_FIELDS: tuple[str, ...] = (
    "eda_report_path",
    "problem_definition_path",
    "solution_plan_path",
    "feature_spec_path",
    "baseline_results_path",
)

_RAG_QUERY_N_RESULTS = 3


def _relative_to_workspace(path: str, workspace: WorkspaceManager) -> str:
    """Re-relativize `path` against `workspace.workspace_path` if absolute.

    A deliberate small duplicate of `src/nodes/llm/base.py::relative_to_workspace`'s
    body, not an import of it: importing from `src/nodes/` would give
    `src/api/` a dependency on the pipeline graph's node layer, which
    `docs/adr/0002-explainer-is-not-an-llmnode.md` states this design
    deliberately avoids.
    """
    p = Path(path)
    if not p.is_absolute():
        return path
    return str(p.relative_to(workspace.workspace_path))


class Explainer:
    """Read-only assistant answering questions about one pipeline run."""

    name = "explainer"

    def __init__(
        self,
        *,
        workspace: WorkspaceManager,
        state_reader: Callable[[], dict[str, Any]],
        rag_store: Any | None = None,
        agent_config_dir: str | Path | None = None,
        prompts_dir: str | Path | None = None,
    ) -> None:
        self.config = load_agent_config(self.name, base_dir=agent_config_dir)
        self.llm = LLMFactory.get(self.config.model_role)
        loader = PromptLoader(prompts_dir) if prompts_dir is not None else PromptLoader()
        self.system_prompt = loader.load(self.name, self.config.prompt_version)
        self._workspace = workspace
        self._state_reader = state_reader
        self._rag_store = rag_store

    def answer(self, question: str, *, history: list[dict[str, str]] | None = None) -> str:
        """Answer `question`, blocking on the LLM call. Never writes anything."""
        state_values = self._state_reader() or {}
        context = self._gather_context(state_values, question)
        messages = self._build_messages(context, history or [], question)
        response = self.llm.invoke(messages)
        return response.content if isinstance(response.content, str) else str(response.content)

    def _gather_context(self, state_values: dict[str, Any], question: str) -> str:
        """Build a text block of everything currently known that might help
        answer `question`: the checkpoint summary (if any), workspace report
        excerpts, and related RAG findings. Any failure reading one piece is
        logged and that piece is simply omitted — never propagates, and this
        method never calls a `WorkspaceManager` write method."""
        sections: list[str] = []

        checkpoint_summary = state_values.get("checkpoint_summary")
        if checkpoint_summary:
            sections.append(f"## Checkpoint summary\n{checkpoint_summary}")

        for field in _CONTEXT_PATH_FIELDS:
            section = self._read_workspace_section(field, state_values.get(field))
            if section:
                sections.append(section)

        rag_section = self._read_rag_section(question)
        if rag_section:
            sections.append(rag_section)

        return "\n\n".join(sections)

    def _read_workspace_section(self, field: str, raw_path: str | None) -> str | None:
        """Read the workspace file named by `raw_path` (a `state_values[field]`
        value), returning a labeled section, or `None` if `raw_path` is falsy
        or the read fails."""
        if not raw_path:
            return None

        relative_path = _relative_to_workspace(raw_path, self._workspace)
        try:
            if relative_path.endswith(".json"):
                content: Any = self._workspace.read_json(relative_path)
            else:
                content = self._workspace.read_text(relative_path)
        except (FileNotFoundError, ValueError, OSError) as exc:
            logger.warning("explainer could not read %s (%s): %s", field, relative_path, exc)
            return None

        return f"## {field}\n{content}"

    def _read_rag_section(self, question: str) -> str | None:
        """Query `self._rag_store` for findings related to `question`. Any
        exception is caught and logged — a RAG failure must never break
        chat."""
        if self._rag_store is None:
            return None

        try:
            hits = self._rag_store.query(question, n_results=_RAG_QUERY_N_RESULTS)
        except Exception as exc:  # noqa: BLE001 - RAG failures must never break chat
            logger.warning("explainer RAG query failed: %s", exc)
            return None

        findings = [hit.key_findings for hit in hits if getattr(hit, "key_findings", None)]
        if not findings:
            return None
        return "## Related past findings\n" + "\n".join(f"- {finding}" for finding in findings)

    def _build_messages(
        self, context: str, history: list[dict[str, str]], question: str
    ) -> list[BaseMessage]:
        messages: list[BaseMessage] = [SystemMessage(self.system_prompt)]
        for turn in history:
            content = turn.get("content", "")
            if turn.get("role") == "assistant":
                messages.append(AIMessage(content))
            else:
                messages.append(HumanMessage(content))

        final_question = f"{context}\n\n## Question\n{question}" if context else question
        messages.append(HumanMessage(final_question))
        return messages
