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

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.config.settings import Settings
from src.memory.store import IndexDocument
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


def _build_kernel_context(kernels: list[KernelSummary]) -> str:
    """Build the `## Kernels` numbered input block injected into the LLM
    prompt. Empty when `kernels` is empty — the v1 prompt handles that case
    explicitly (see its "no kernels found" instruction)."""
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


def _strip_outer_fence(content: str, node_name: str) -> str:
    """Strip a single outer fence wrapping the entire response, if present.
    Mirrors `leakage_auditor._strip_outer_fence`'s outermost-marker anchoring
    (never truncates on an embedded ``` inside a JSON string value)."""
    text = content.strip()
    if not text.startswith("```"):
        return text
    if not text.endswith("```") or len(text) < 6:
        raise ValueError(f"{node_name} response starts with a fence but never closes it")
    first_newline = text.find("\n")
    if first_newline == -1:
        raise ValueError(f"{node_name} response fence has no content")
    inner = text[first_newline + 1 :]
    closing_idx = inner.rfind("```")
    if closing_idx == -1:
        raise ValueError(f"{node_name} response fence has no closing delimiter")
    return inner[:closing_idx].strip()


def _extract_json_array(content: str, node_name: str) -> list[Any]:
    """Parse the LLM's JSON array response. Accepts raw JSON with no fence,
    or the entire response wrapped in a single ```json or unlabeled ``` fence.
    Raises `ValueError` (naming `node_name`) on malformed JSON or a top-level
    value that isn't a JSON array."""
    text = _strip_outer_fence(content, node_name)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{node_name} response is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise ValueError(f"{node_name} response must be a JSON array, got {type(data).__name__}")
    return data


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _coerce_relevance_score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _build_index_documents(
    kernels: list[KernelSummary], extractions: list[Any], node_name: str
) -> list[IndexDocument]:
    """Build one `IndexDocument` per extraction. `.text` is the RAW kernel
    metadata (title/author/votes) — never the LLM's summary, so the embedded
    text stays grounded in verifiable evidence. Raises `ValueError` if an
    extraction's `index` doesn't refer to a valid 1-based kernel position."""
    documents: list[IndexDocument] = []
    for extraction in extractions:
        if not isinstance(extraction, dict):
            raise ValueError(
                f"{node_name} extraction entries must be JSON objects, got "
                f"{type(extraction).__name__}"
            )

        index = extraction.get("index")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not (1 <= index <= len(kernels))
        ):
            raise ValueError(
                f"{node_name} extraction 'index' must be an int in [1, {len(kernels)}], "
                f"got {index!r}"
            )

        kernel = kernels[index - 1]
        documents.append(
            IndexDocument(
                text=(
                    f"Title: {kernel.title}\n"
                    f"Author: {kernel.author}\n"
                    f"Total votes: {kernel.total_votes}\n"
                    f"URL: {kernel.url}"
                ),
                source=kernel.url,
                problem_type=_coerce_str_list(extraction.get("problem_type")),
                methods_used=_coerce_str_list(extraction.get("methods_used")),
                dataset_characteristics=_coerce_str_list(extraction.get("dataset_characteristics")),
                key_findings=str(extraction.get("key_findings") or ""),
                relevance_score=_coerce_relevance_score(extraction.get("relevance_score")),
            )
        )
    return documents


def _render_report_markdown(kernels: list[KernelSummary], documents: list[IndexDocument]) -> str:
    """Human-readable report combining the raw kernel list and the LLM's
    structured extractions, one section per document in extraction order."""
    lines = ["# Competition analysis — top-voted kernels", ""]

    if not kernels:
        lines.append("No public kernels were found for this competition.")
        return "\n".join(lines)

    lines.append(f"Analyzed {len(kernels)} top-voted kernel(s); {len(documents)} indexed to RAG.")
    lines.append("")

    for doc in documents:
        lines.append(f"## {doc.source}")
        lines.append("")
        lines.append(f"- Problem type: {', '.join(doc.problem_type) or '(none extracted)'}")
        lines.append(f"- Methods used: {', '.join(doc.methods_used) or '(none extracted)'}")
        lines.append(
            "- Dataset characteristics: "
            f"{', '.join(doc.dataset_characteristics) or '(none extracted)'}"
        )
        lines.append(f"- Relevance score: {doc.relevance_score}")
        lines.append("")
        lines.append(f"Key findings: {doc.key_findings or '(none extracted)'}")
        lines.append("")

    return "\n".join(lines)


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
        extractions = _extract_json_array(content, self.name)
        documents = _build_index_documents(self._kernels, extractions, self.name)

        rag_store = self._rag_store or self._build_default_rag_store()
        rag_store.index(documents)

        report = _render_report_markdown(self._kernels, documents)
        return workspace.write_text(relative_path, report)

    def _build_default_rag_store(self) -> RagStore:
        """Lazily construct a `RagStore` scoped to this run's competition
        when none was injected, pointed at the Docker `chroma` service per
        `config/settings.yaml`'s `workspace.chroma_host`/`chroma_port` (see
        `docs/pipeline.md` § rag)."""
        settings = Settings.load()
        return RagStore(
            self._competition_name,
            chroma_host=settings.workspace.chroma_host,
            chroma_port=settings.workspace.chroma_port,
        )
