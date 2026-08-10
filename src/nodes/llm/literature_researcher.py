"""literature_researcher: queries arxiv + Semantic Scholar for research
relevant to the competition's problem, has the LLM extract structured
`IndexDocument` metadata per source, indexes them into the competition's
`RagStore`, and writes `reports/literature_research.md`.

Runs in Pipeline Phase 2 (Research), in parallel with `web_researcher`
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
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage

from src.config.settings import Settings
from src.nodes.llm._research_common import (
    SearchClient,
    SourceDocument,
    build_index_documents,
    build_ml_techniques_query,
    build_source_context,
    extract_json_array,
    render_report_markdown,
)
from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.tools.rag import RagStore
from src.workspace.workspace_manager import WorkspaceManager

_ARXIV_API_URL = "https://export.arxiv.org/api/query"
_SEMANTIC_SCHOLAR_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_ARXIV_ATOM_NS = "{http://www.w3.org/2005/Atom}"


class LiteratureSearchClient:
    """Production `SearchClient`: merges arxiv + Semantic Scholar results.

    Stdlib `urllib.request` only — no new HTTP dependency. No API key is
    required for basic Semantic Scholar use; `SEMANTIC_SCHOLAR_API_KEY`, if
    set, is attached to raise rate limits.
    """

    def __init__(self, *, max_results_per_source: int = 3, timeout: float = 10.0) -> None:
        self._max_results_per_source = max_results_per_source
        self._timeout = timeout

    def search(self, query: str) -> list[SourceDocument]:
        return self._search_arxiv(query) + self._search_semantic_scholar(query)

    def _search_arxiv(self, query: str) -> list[SourceDocument]:
        params = urllib.parse.urlencode(
            {"search_query": f"all:{query}", "max_results": self._max_results_per_source}
        )
        with urllib.request.urlopen(
            f"{_ARXIV_API_URL}?{params}", timeout=self._timeout
        ) as response:
            raw = response.read()

        root = ET.fromstring(raw)
        return [
            SourceDocument(
                title=(entry.findtext(f"{_ARXIV_ATOM_NS}title") or "").strip(),
                text=(entry.findtext(f"{_ARXIV_ATOM_NS}summary") or "").strip(),
                url=(entry.findtext(f"{_ARXIV_ATOM_NS}id") or "").strip(),
            )
            for entry in root.findall(f"{_ARXIV_ATOM_NS}entry")
        ]

    def _search_semantic_scholar(self, query: str) -> list[SourceDocument]:
        params = urllib.parse.urlencode(
            {
                "query": query,
                "fields": "title,abstract,url",
                "limit": self._max_results_per_source,
            }
        )
        request = urllib.request.Request(f"{_SEMANTIC_SCHOLAR_API_URL}?{params}")
        api_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
        if api_key:
            request.add_header("x-api-key", api_key)

        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read())

        return [
            SourceDocument(
                title=paper.get("title") or "",
                text=paper.get("abstract") or "",
                url=paper.get("url") or "",
            )
            for paper in payload.get("data", [])
        ]


class LiteratureResearcherNode(LLMNode):
    name = "literature_researcher"

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
            self._client = LiteratureSearchClient()
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

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        self._competition_name = state["competition_name"]
        query = build_ml_techniques_query(state)
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
        report = render_report_markdown("Literature Research", self._sources, documents)
        return workspace.write_text(relative_path, report)
