"""web_researcher: queries the Tavily web search API for content relevant to
the competition's problem, has the LLM extract structured `IndexDocument`
metadata per source, indexes them into the competition's `RagStore`, and
writes `reports/web_research.md`.

Runs in Pipeline Phase 2 (Research), in parallel with `literature_researcher`
(`config/phases/phase2_research.yaml`'s `parallel_nodes`) — see
docs/pipeline.md § State's concurrent-write note for why neither research
node may own a `LabState` path field of its own (only `messages` has a
LangGraph reducer, and both nodes run in the same Phase-2 super-step).

Overrides `_build_messages` (run the search, inject the source list as an
extra HumanMessage) and `_write_output` (extract per-source metadata, index
into `RagStore`, write the markdown report). Does NOT override
`_build_output_state` — inherits `LLMNode`'s `{}` default.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage

from src.config.settings import Settings
from src.nodes.llm._research_common import (
    SearchClient,
    SourceDocument,
    build_index_documents,
    build_source_context,
    extract_json_array,
    relative_to_workspace,
    render_report_markdown,
)
from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.tools.rag import RagStore
from src.workspace.workspace_manager import WorkspaceManager

_TAVILY_API_URL = "https://api.tavily.com/search"


class WebSearchClient:
    """Production `SearchClient`: one HTTPS POST to the Tavily search API.

    Stdlib `urllib.request` only — no new HTTP dependency. Raises
    `RuntimeError` immediately if `TAVILY_API_KEY` is missing, before
    attempting any network call.
    """

    def __init__(self, *, max_results: int = 5, timeout: float = 10.0) -> None:
        self._max_results = max_results
        self._timeout = timeout

    def search(self, query: str) -> list[SourceDocument]:
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            raise RuntimeError("TAVILY_API_KEY is not set")

        body = json.dumps(
            {"api_key": api_key, "query": query, "max_results": self._max_results}
        ).encode("utf-8")
        request = urllib.request.Request(
            _TAVILY_API_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read())

        return [
            SourceDocument(
                title=result.get("title") or "",
                text=result.get("content") or "",
                url=result.get("url") or "",
            )
            for result in payload.get("results", [])
        ]


class WebResearcherNode(LLMNode):
    name = "web_researcher"

    def __init__(
        self,
        *,
        client: SearchClient | None = None,
        rag_store: RagStore | None = None,
        agent_config_dir: str | Path | None = None,
        prompts_dir: str | Path | None = None,
    ) -> None:
        super().__init__(agent_config_dir=agent_config_dir, prompts_dir=prompts_dir)
        self._client = client
        self._rag_store = rag_store
        self._sources: list[SourceDocument] = []
        self._competition_name: str = ""

    def _ensure_client(self) -> SearchClient:
        if self._client is None:
            self._client = WebSearchClient()
        return self._client

    def _ensure_rag_store(self, competition_name: str) -> RagStore:
        if self._rag_store is None:
            settings = Settings.load()
            self._rag_store = RagStore(
                competition_name,
                settings.workspace.chroma_host,
                settings.workspace.chroma_port,
            )
        return self._rag_store

    def _build_query(self, state: LabState) -> str:
        problem_type = self._read_problem_type(state)
        if problem_type:
            return f"{problem_type} machine learning techniques for {state['competition_name']}"
        return f"machine learning techniques for {state['competition_name']}"

    def _read_problem_type(self, state: LabState) -> str:
        path = state["problem_definition_path"]
        if not path:
            return ""
        workspace = WorkspaceManager(state["workspace_path"])
        try:
            problem_definition = workspace.read_json(relative_to_workspace(path, workspace))
        except OSError:
            return ""
        problem_type = problem_definition.get("problem_type")
        return problem_type if isinstance(problem_type, str) else ""

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        self._competition_name = state["competition_name"]
        query = self._build_query(state)
        self._sources = self._ensure_client().search(query)
        messages.append(
            HumanMessage(content=f"## Query\n\n{query}\n\n{build_source_context(self._sources)}")
        )
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        extractions = extract_json_array(content, self.name)
        documents = build_index_documents(self._sources, extractions, self.name)
        self._ensure_rag_store(self._competition_name).index(documents)
        report = render_report_markdown("Web Research", self._sources, documents)
        return workspace.write_text(relative_path, report)
