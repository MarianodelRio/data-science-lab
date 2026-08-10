"""memory_manager: consolidates a retrieved window of the competition's
`RagStore` after Phase 2's other research nodes have indexed their findings,
deduplicating near-identical entries and re-scoring relevance before later
phases (architect, feature engineer, specialists, ...) start querying the
store for "what did we already try?".

Runs in Pipeline Phase 2 (Research), last in `config/phases/phase2_research.yaml`'s
`sequence`, after `literature_researcher`, `web_researcher`, and
`competition_analyst` have all indexed their findings into the same
competition's `RagStore` collection.

## Scope: query-window consolidation, not corpus-wide dedup

`RagStore` (`src/tools/rag.py`, a protected contract) exposes only `index()`
(upsert-by-id) and `query()` (similarity search) — there is no
list-all-documents or delete() API to scan and prune an entire Chroma
collection. So this node does not attempt corpus-wide deduplication. Instead
it queries a representative window of candidates (`_QUERY_N_RESULTS`, wider
than `RagStore.query`'s own default of 10), has the LLM partition that window
into clusters of near-duplicates, merges each cluster into one consolidated
`IndexDocument`, and re-indexes it under a reused `.id` (one of the cluster
members' original id) so `RagStore.index()`'s upsert semantics collapse that
row in place. A near-duplicate that the query window never surfaces (i.e. it
isn't similar enough to the query text to rank among the top
`_QUERY_N_RESULTS` results) is left untouched by this pass — this is a
narrower guarantee than "the whole store is deduplicated."

**Caveat — non-canonical rows are not physically deleted.** When a cluster of
N candidates merges into 1 consolidated document, only the canonical id gets
the merged content on upsert; the other N-1 candidates' original rows remain
in Chroma, stale, since `RagStore` has no `delete()`. They will keep showing
up in future `.query()` calls until a future `RagStore`/`IndexDocument`
enhancement adds deterministic ids and/or delete support (see
`context/discoveries.md`'s T-017 OPEN entry on `IndexDocument.id` generation,
which this node's caveat is a direct consequence of).

No separate "query memory" helper is exposed by this module: `RagStore.query()`
is already public and importable. Any future node that wants "what did we
already try that failed?" just instantiates its own `RagStore` (same pattern
as `_ensure_rag_store` below) and calls `.query()` directly.

Overrides `_build_messages` (query the store, inject the candidate window as
an extra HumanMessage) and `_write_output` (extract per-cluster metadata,
re-index the consolidated documents, write the markdown report). Does NOT
override `_build_output_state` — memory_manager owns no `LabState` field of
its own, so it inherits `LLMNode`'s `{}` default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.config.settings import Settings
from src.memory.store import IndexDocument
from src.nodes.llm._research_common import build_ml_techniques_query, extract_json_array
from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.tools.rag import RagStore
from src.workspace.workspace_manager import WorkspaceManager

_QUERY_N_RESULTS = 20
_MIN_RELEVANCE_SCORE = 0.0
_MAX_RELEVANCE_SCORE = 1.0


def _validate_cluster_indices(entry: Any, node_name: str) -> list[int]:
    """Validate and return one cluster entry's `indices` field: a non-empty
    list of unique ints (bool excluded)."""
    if not isinstance(entry, dict):
        raise ValueError(f"{node_name} cluster entry must be a JSON object, got {entry!r}")

    indices = entry.get("indices")
    if (
        not isinstance(indices, list)
        or not indices
        or not all(isinstance(i, int) and not isinstance(i, bool) for i in indices)
    ):
        raise ValueError(
            f"{node_name} cluster entry field 'indices' must be a non-empty list of "
            f"integers, got {indices!r}"
        )
    if len(set(indices)) != len(indices):
        raise ValueError(
            f"{node_name} cluster entry field 'indices' must not contain duplicates, "
            f"got {indices!r}"
        )
    return indices


def _validate_clusters_partition_candidates(
    clusters_indices: list[list[int]], candidate_count: int, node_name: str
) -> None:
    """Every candidate `1..candidate_count` must appear in exactly one
    cluster's `indices` — no gaps, no overlaps across clusters."""
    flat = [index for indices in clusters_indices for index in indices]
    expected = list(range(1, candidate_count + 1))
    if sorted(flat) != expected:
        raise ValueError(
            f"{node_name} clusters must partition candidates 1..{candidate_count} exactly "
            f"(no gaps, no overlaps), got indices {sorted(flat)!r}"
        )


def _validate_str_list_field(value: Any, field_name: str, node_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(
            f"{node_name} cluster entry field {field_name!r} must be a list of strings, "
            f"got {value!r}"
        )
    return value


def _validate_key_findings_field(value: Any, node_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(
            f"{node_name} cluster entry field 'key_findings' must be a string, got {value!r}"
        )
    return value


def _validate_relevance_score_field(value: Any, node_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{node_name} cluster entry field 'relevance_score' must be a number, got {value!r}"
        )
    if not (_MIN_RELEVANCE_SCORE <= value <= _MAX_RELEVANCE_SCORE):
        raise ValueError(
            f"{node_name} cluster entry field 'relevance_score' must be within "
            f"[{_MIN_RELEVANCE_SCORE}, {_MAX_RELEVANCE_SCORE}], got {value!r}"
        )
    return float(value)


def _build_consolidated_documents(
    candidates: list[IndexDocument], clusters: list[Any], node_name: str
) -> list[IndexDocument]:
    """Validate `clusters` (one entry per output cluster, 1-based `indices`
    into `candidates`) and merge each cluster's member documents into one
    consolidated `IndexDocument`, keyed by the canonical member's original
    `.id` (the lowest original 1-based index in that cluster) so re-indexing
    it upserts in place.

    Returns `[]` when `candidates` is empty (mirrors
    `build_index_documents`'s empty-input short circuit in
    `_research_common.py`) — `clusters` is not even inspected in that case.
    """
    if not candidates:
        return []

    if not isinstance(clusters, list):
        raise ValueError(
            f"{node_name} response must be a JSON array, got {type(clusters).__name__}"
        )

    clusters_indices = [_validate_cluster_indices(entry, node_name) for entry in clusters]
    _validate_clusters_partition_candidates(clusters_indices, len(candidates), node_name)

    consolidated: list[IndexDocument] = []
    for entry, indices in zip(clusters, clusters_indices, strict=True):
        members = [candidates[i - 1] for i in sorted(indices)]
        canonical_id = members[0].id
        text = "\n\n".join(member.text for member in members)
        source = "; ".join(dict.fromkeys(member.source for member in members))

        consolidated.append(
            IndexDocument(
                id=canonical_id,
                text=text,
                source=source,
                problem_type=_validate_str_list_field(
                    entry.get("problem_type"), "problem_type", node_name
                ),
                methods_used=_validate_str_list_field(
                    entry.get("methods_used"), "methods_used", node_name
                ),
                dataset_characteristics=_validate_str_list_field(
                    entry.get("dataset_characteristics"), "dataset_characteristics", node_name
                ),
                key_findings=_validate_key_findings_field(entry.get("key_findings"), node_name),
                relevance_score=_validate_relevance_score_field(
                    entry.get("relevance_score"), node_name
                ),
            )
        )

    return consolidated


def _build_candidate_context(candidates: list[IndexDocument]) -> str:
    """Render `candidates` as a numbered `### Candidate {i}` block for
    injection into the prompt as an extra human message. Mirrors
    `build_source_context`'s empty-case style in `_research_common.py`."""
    if not candidates:
        return "## Candidates\n\nNo documents are currently in memory for this query."

    lines = ["## Candidates", ""]
    for index, candidate in enumerate(candidates, start=1):
        problem_type = ", ".join(candidate.problem_type) or "none identified"
        methods_used = ", ".join(candidate.methods_used) or "none identified"
        dataset_characteristics = ", ".join(candidate.dataset_characteristics) or "none identified"
        lines.extend(
            [
                f"### Candidate {index}",
                f"Source: {candidate.source}",
                f"Problem type: {problem_type}",
                f"Methods used: {methods_used}",
                f"Dataset characteristics: {dataset_characteristics}",
                f"Key findings: {candidate.key_findings}",
                f"Relevance score: {candidate.relevance_score}",
                "",
                candidate.text,
                "",
            ]
        )
    return "\n".join(lines).strip()


def _render_report(candidates: list[IndexDocument], consolidated: list[IndexDocument]) -> str:
    """Human-readable markdown report: `# Memory Consolidation`, a summary
    line, and one `### ` subsection per consolidated document. Handles the
    empty-candidates case with an explicit line instead of a spurious 0/0
    report."""
    lines = ["# Memory Consolidation", ""]
    if not candidates:
        lines.append("No documents were retrieved for this query.")
        return "\n".join(lines).strip() + "\n"

    lines.append(
        f"Retrieved {len(candidates)} candidate(s); consolidated into "
        f"{len(consolidated)} record(s)."
    )
    lines.append("")
    for document in consolidated:
        problem_type = ", ".join(document.problem_type) or "none identified"
        methods_used = ", ".join(document.methods_used) or "none identified"
        lines.extend(
            [
                f"### {document.id}",
                f"- Source(s): {document.source}",
                f"- Key findings: {document.key_findings}",
                f"- Problem type: {problem_type}",
                f"- Methods used: {methods_used}",
                f"- Relevance score: {document.relevance_score}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


class MemoryManagerNode(LLMNode):
    name = "memory_manager"

    def __init__(
        self,
        *,
        rag_store: RagStore | None = None,
        agent_config_dir: str | Path | None = None,
        prompts_dir: str | Path | None = None,
    ) -> None:
        super().__init__(agent_config_dir=agent_config_dir, prompts_dir=prompts_dir)
        self._rag_store = rag_store
        self._candidates: list[IndexDocument] = []
        self._competition_name: str = ""

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
        self._candidates = self._ensure_rag_store(self._competition_name).query(
            query, n_results=_QUERY_N_RESULTS
        )
        candidate_context = _build_candidate_context(self._candidates)
        messages.append(HumanMessage(content=f"## Query\n\n{query}\n\n{candidate_context}"))
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        clusters = extract_json_array(content, self.name)
        consolidated = _build_consolidated_documents(self._candidates, clusters, self.name)
        self._ensure_rag_store(self._competition_name).index(consolidated)
        return workspace.write_text(relative_path, _render_report(self._candidates, consolidated))
