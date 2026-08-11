"""Shared base class every LLM-calling pipeline node subclasses.

Convention (docs/pipeline.md § Node-module convention, src/graph/node_resolver.py):
a node lives at `src/nodes/llm/{name}.py`, exposes one class with a plain
class-level `name` attribute, constructible with zero arguments (`cls()`).
`LLMNode` is deliberately a plain class, NOT a `pydantic.BaseModel` — see
node_resolver's module docstring for why a Pydantic typed field wouldn't
satisfy its `getattr(obj, "name", None)` lookup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.schema import AgentConfig
from src.config.settings import Settings
from src.llm.factory import LLMFactory
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager


def trim_context(messages: list[BaseMessage], max_messages_per_node: int) -> list[BaseMessage]:
    """Keep only the last `max_messages_per_node` messages (last_n_messages strategy,
    settings.context.trim_strategy). A non-positive `max_messages_per_node` returns
    `[]` explicitly — `messages[-0:]` returns the *full* list in Python, not an empty
    one, so `n <= 0` must be special-cased rather than relying on the slice alone.
    """
    if max_messages_per_node <= 0:
        return []
    return messages[-max_messages_per_node:]


def relative_to_workspace(path: str, workspace: WorkspaceManager) -> str:
    """`WorkspaceManager.write_text`/`write_json` return an *absolute* path
    (design.md's WorkspaceManager API table: `write_json(...) -> ...  #
    returns abs path`), and `LLMNode._build_output_state` implementations
    store that return value verbatim into `LabState` path fields (e.g.
    `eda_report_path`, `problem_definition_path`). But `read_text`/
    `read_json` require a *relative* path and reject absolute ones. Nodes
    that consume an upstream node's path field must therefore re-relativize
    it against the current workspace root before reading — already-relative
    input (e.g. in unit tests) passes through unchanged.

    Hoisted (T-020) from three byte-for-byte-identical private copies
    (`_relative_to_workspace`) previously duplicated in `problem_framer.py`,
    `leakage_auditor.py`, and `analysis_critic.py`. `src/nodes/llm/_research_common.py`
    keeps its own separate copy (Phase 2 research nodes, out of T-020's scope).
    """
    p = Path(path)
    if not p.is_absolute():
        return path
    return str(p.relative_to(workspace.workspace_path))


class LLMNode:
    """Base class for every LLM-calling node. Subclasses declare a class-level
    `name` matching their `config/agents/{name}.yaml` / `config/prompts/{name}/…`
    filename stem; that's the only required override for a minimal node.
    """

    name: str = ""

    def __init__(
        self,
        *,
        agent_config_dir: str | Path | None = None,
        prompts_dir: str | Path | None = None,
    ) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} must declare a non-empty class-level 'name'")
        self.config: AgentConfig = load_agent_config(self.name, base_dir=agent_config_dir)
        self.llm: BaseChatModel = LLMFactory.get(self.config.model_role)
        loader = PromptLoader(prompts_dir) if prompts_dir is not None else PromptLoader()
        self.system_prompt: str = loader.load(self.name, self.config.prompt_version)
        self._max_messages_per_node: int = Settings.load().context.max_messages_per_node

    def __call__(self, state: LabState) -> dict[str, Any]:
        trimmed = trim_context(state["messages"], self._max_messages_per_node)
        messages = self._build_messages(trimmed, state)
        response = self.llm.invoke(messages)

        workspace = WorkspaceManager(state["workspace_path"])
        relative_path = self._resolve_output_path(state)
        written_path = self._write_output(workspace, relative_path, response)

        delta: dict[str, Any] = {"messages": [response]}
        delta.update(self._build_output_state(written_path, state))
        return delta

    # -- extension points for concrete agent subclasses --

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        """Default: system prompt + trimmed history. Override to inject
        node-specific input (e.g. EDA report text) as an extra HumanMessage."""
        return [SystemMessage(content=self.system_prompt), *trimmed_messages]

    def _resolve_output_path(self, state: LabState) -> str:
        """Default: interpolate `{iteration}` in `output_file_pattern` from
        `state['current_iteration']`. Override if a pattern needs other placeholders.
        `output_file_pattern` may legitimately omit `{iteration}` entirely for
        one-time/frozen outputs (e.g. `fold_config.json`, `eda_report.md` — see
        design.md's workspace layout and CLAUDE.md invariant #1); a fixed path is
        correct there, not a bug — `str.format` harmlessly ignores an unused
        `iteration` kwarg in that case."""
        try:
            return self.config.output_file_pattern.format(iteration=state["current_iteration"])
        except KeyError as e:
            raise ValueError(
                f"output_file_pattern {self.config.output_file_pattern!r} for agent "
                f"'{self.name}' has an unresolved placeholder {e}; override "
                "_resolve_output_path for non-iteration placeholders"
            ) from e

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        """Default: write the raw LLM text via `WorkspaceManager.write_text`.
        Override (e.g. to call `workspace.write_json`) for structured-output nodes."""
        content = response.content if isinstance(response.content, str) else str(response.content)
        return workspace.write_text(relative_path, content)

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        """Default: no extra state beyond `messages`. Override to set the `LabState`
        path field this node owns, e.g. `{"solution_plan_path": written_path}` — keep
        it minimal per LabState's LastValue-channel concurrency note (design.md)."""
        return {}
