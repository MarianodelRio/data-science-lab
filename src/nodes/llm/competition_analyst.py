"""competition_analyst: pulls Kaggle's top-voted public kernels for the
current competition, has the LLM extract winning patterns from their
metadata, and indexes the results into the competition's `RagStore`.

Overrides `_build_messages` (fetch kernels via `kernel_lister`, store them on
`self` for `_write_output` to reuse, and append a HumanMessage with the
kernel context block) and `_write_output` (parse the LLM's JSON array
response, build `IndexDocument`s, index them into a `RagStore`, and write a
human-readable markdown report via `workspace.write_text`). Does NOT override
`_build_output_state` — there is no `LabState` field for this node's output,
mirroring `leakage_auditor`.

JSON-array parsing, extraction validation, and `IndexDocument`/report
construction are NOT re-implemented here: this module reuses
`src.nodes.llm._research_common`'s `extract_json_array`/`build_index_documents`/
`render_report_markdown` — the same helpers `literature_researcher.py` and
`web_researcher.py` (T-017) already share — rather than duplicating a weaker
copy. In particular `build_index_documents` enforces `relevance_score` in
`[0.0, 1.0]` and that extraction indices exactly cover `1..len(kernels)` (no
gaps, no duplicates), which a from-scratch reimplementation here previously
did not. Each `KernelSummary` is adapted into a `SourceDocument(title, text,
url)` — `text` packs the kernel's author/vote-count metadata — before being
handed to those shared helpers, mirroring `web_researcher._build_messages`/
`_write_output`'s own usage exactly.

**Evidentiary limitation** (see `config/prompts/competition_analyst/v1.md`):
only kernel title/author/vote-count are available as evidence — never
notebook code/output — so the LLM is instructed to leave `methods_used`/
`key_findings` empty rather than guess when a title doesn't reveal the
method.

**Why kernels are stashed on `self` instead of re-fetched in `_write_output`**:
`LLMNode._write_output`'s signature (`workspace`, `relative_path`, `response`)
carries no `state`/kernel-list parameter, and re-calling `kernel_lister` a
second time would be wasteful and could return a different result than what
the LLM actually saw. This is safe because `competition_analyst` is not one
of Pipeline Phase 2's `parallel_nodes` (only `literature_researcher` and
`web_researcher` run concurrently per `config/phases/phase2_research.yaml`),
so a single node instance is never re-entered while a prior `__call__` is
still in flight.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.config.settings import Settings
from src.nodes.llm._research_common import (
    SourceDocument,
    build_index_documents,
    extract_json_array,
    render_report_markdown,
)
from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.tools.kaggle_client import list_top_kernels
from src.tools.rag import RagStore
from src.workspace.workspace_manager import WorkspaceManager

_NODE_NAME = "competition_analyst"


@dataclass(frozen=True)
class KernelSummary:
    ref: str
    title: str
    author: str
    total_votes: int
    url: str


def _to_kernel_summary(kernel: dict[str, Any]) -> KernelSummary:
    return KernelSummary(
        ref=str(kernel["ref"]),
        title=str(kernel["title"]),
        author=str(kernel["author"]),
        total_votes=int(kernel["total_votes"]),
        url=str(kernel["url"]),
    )


def _to_source_document(kernel: KernelSummary) -> SourceDocument:
    """Adapt a `KernelSummary` into the `SourceDocument(title, text, url)`
    shape `_research_common`'s `build_index_documents`/`render_report_markdown`
    expect. `text` carries the kernel's author/vote-count metadata so the
    `IndexDocument.text` those helpers build (`f"{title}\\n\\n{text}"`) stays
    the RAW kernel metadata — never the LLM's summary — same intent as
    `web_researcher`'s abstract/snippet `text`, just kernel-shaped."""
    return SourceDocument(
        title=kernel.title,
        text=f"Author: {kernel.author}\nTotal votes: {kernel.total_votes}",
        url=kernel.url,
    )


def _build_kernel_context(kernels: list[KernelSummary]) -> str:
    """Build the `## Kernels` numbered input block injected into the LLM
    prompt. Empty when `kernels` is empty — the v1 prompt handles that case
    explicitly (see its "no kernels found" instruction). Kept
    kernel-specific (distinct heading/fields from `_research_common`'s
    generic `build_source_context`) since `config/prompts/competition_analyst/
    v1.md` documents this exact `## Kernels` / `### Kernel {i}` shape."""
    if not kernels:
        return "## Kernels\n\n(no kernels found for this competition)"

    blocks = [
        f"### Kernel {i}\n"
        f"- Title: {kernel.title}\n"
        f"- Author: {kernel.author}\n"
        f"- Total votes: {kernel.total_votes}\n"
        f"- URL: {kernel.url}"
        for i, kernel in enumerate(kernels, start=1)
    ]
    return "## Kernels\n\n" + "\n\n".join(blocks)


class CompetitionAnalystNode(LLMNode):
    name = _NODE_NAME

    def __init__(
        self,
        *,
        kernel_lister: Any = None,
        rag_store: RagStore | None = None,
        top_n: int = 10,
        agent_config_dir: str | None = None,
        prompts_dir: str | None = None,
    ) -> None:
        super().__init__(agent_config_dir=agent_config_dir, prompts_dir=prompts_dir)
        # Defaults to the bare module-level function — no eager instantiation,
        # no credential check at construction time. `list_top_kernels` only
        # touches `kaggle`/its credentials once actually called with a real
        # `api=None`, which never happens in this module's own tests (a fake
        # `kernel_lister` is always injected there).
        self._kernel_lister = kernel_lister if kernel_lister is not None else list_top_kernels
        self._rag_store = rag_store
        self._top_n = top_n
        self._kernels: list[KernelSummary] = []
        self._competition_name: str = ""

    def _ensure_rag_store(self, competition_name: str) -> RagStore:
        """Lazily construct + cache a `RagStore` scoped to this run's
        competition when none was injected, pointed at the Docker `chroma`
        service per `config/settings.yaml`'s `workspace.chroma_host`/
        `chroma_port` — mirrors `web_researcher._ensure_rag_store` exactly."""
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
        base = super()._build_messages(trimmed_messages, state)
        self._competition_name = state["competition_name"]
        raw_kernels = self._kernel_lister(self._competition_name, n=self._top_n)
        self._kernels = [_to_kernel_summary(kernel) for kernel in raw_kernels]
        return [*base, HumanMessage(content=_build_kernel_context(self._kernels))]

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        sources = [_to_source_document(kernel) for kernel in self._kernels]
        extractions = extract_json_array(content, self.name)
        documents = build_index_documents(sources, extractions, self.name)

        self._ensure_rag_store(self._competition_name).index(documents)

        report = render_report_markdown("Competition Analysis", sources, documents)
        return workspace.write_text(relative_path, report)
