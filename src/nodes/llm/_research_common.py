"""Shared, non-node helpers for the two Pipeline Phase 2 research nodes
(`literature_researcher`, `web_researcher`).

This module declares no class matching its own filename stem
(`_research_common`), so `src/graph/node_resolver.py`'s `_find_node_class`
never mistakes it for a node module — see docs/pipeline.md § Node-module
convention. It is imported by the two node modules but never referenced in
`config/phases/*.yaml`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.memory.store import IndexDocument
from src.workspace.workspace_manager import WorkspaceManager

_MIN_RELEVANCE_SCORE = 0.0
_MAX_RELEVANCE_SCORE = 1.0
_EXTRACTION_LIST_FIELDS = ("problem_type", "methods_used", "dataset_characteristics")


@dataclass(frozen=True)
class SourceDocument:
    """One raw search result, before LLM extraction."""

    title: str
    text: str  # abstract/snippet
    url: str


class SearchClient(Protocol):
    """Injectable search dependency for both research nodes. A bare object
    exposing `.search(query) -> list[SourceDocument]` satisfies this without
    inheriting anything, so tests can pass a minimal fake."""

    def search(self, query: str) -> list[SourceDocument]: ...


def relative_to_workspace(path: str, workspace: WorkspaceManager) -> str:
    """Re-relativize an absolute upstream `LabState` path against the
    workspace root; already-relative input passes through unchanged.

    `WorkspaceManager.write_text`/`write_json` return an *absolute* path,
    and `LLMNode._build_output_state` implementations store that return
    value verbatim into `LabState` path fields (e.g. `problem_definition_path`).
    But `read_text`/`read_json` require a *relative* path and reject absolute
    ones. Identical logic to `problem_framer._relative_to_workspace`/
    `leakage_auditor._relative_to_workspace`.
    """
    p = Path(path)
    if not p.is_absolute():
        return path
    return str(p.relative_to(workspace.workspace_path))


def build_source_context(sources: list[SourceDocument]) -> str:
    """Render `sources` as a numbered `### Source {i}` block (title/URL/text)
    for injection into the prompt as an extra human message."""
    if not sources:
        return "## Sources\n\nNo sources were found for this query."

    lines = ["## Sources", ""]
    for index, source in enumerate(sources, start=1):
        lines.extend(
            [
                f"### Source {index}",
                f"Title: {source.title}",
                f"URL: {source.url}",
                "",
                source.text,
                "",
            ]
        )
    return "\n".join(lines).strip()


def _strip_outer_fence(content: str, node_name: str) -> str:
    """Strip a single outer fence wrapping the entire response, if present.

    Same outer-fence-stripping approach as `problem_framer._extract_json`/
    `leakage_auditor._extract_json`: anchors on the outermost ``` markers so
    an embedded ``` inside a string value (e.g. a quoted code snippet in
    `key_findings`) doesn't get mistaken for the closing fence.
    """
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


def extract_json_array(content: str, node_name: str) -> list[Any]:
    """Extract a top-level JSON array from an LLM response.

    Accepts raw JSON with no fence, or the entire response wrapped in a
    single ```json or unlabeled ``` fence. Raises `ValueError` naming
    `node_name` if the content isn't valid JSON or the top-level value isn't
    a list.
    """
    text = _strip_outer_fence(content, node_name)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{node_name} response is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise ValueError(f"{node_name} response must be a JSON array, got {type(data).__name__}")
    return data


def _validate_extraction_index(entry: Any, node_name: str) -> int:
    if not isinstance(entry, dict):
        raise ValueError(f"{node_name} extraction entry must be a JSON object, got {entry!r}")
    index = entry.get("index")
    if not isinstance(index, int) or isinstance(index, bool):
        raise ValueError(
            f"{node_name} extraction entry missing a valid integer 'index', got {index!r}"
        )
    return index


def _validate_indices_cover_sources(indices: list[int], source_count: int, node_name: str) -> None:
    expected = list(range(1, source_count + 1))
    if sorted(indices) != expected:
        raise ValueError(
            f"{node_name} extraction indices {sorted(indices)!r} must exactly cover "
            f"1..{source_count} with no gaps or duplicates"
        )


def _validate_str_list(value: Any, field_name: str, node_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(
            f"{node_name} extraction field {field_name!r} must be a list of strings, got {value!r}"
        )
    return value


def _validate_key_findings(value: Any, node_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(
            f"{node_name} extraction field 'key_findings' must be a string, got {value!r}"
        )
    return value


def _validate_relevance_score(value: Any, node_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{node_name} extraction field 'relevance_score' must be a number, got {value!r}"
        )
    if not (_MIN_RELEVANCE_SCORE <= value <= _MAX_RELEVANCE_SCORE):
        raise ValueError(
            f"{node_name} extraction field 'relevance_score' must be within "
            f"[{_MIN_RELEVANCE_SCORE}, {_MAX_RELEVANCE_SCORE}], got {value!r}"
        )
    return float(value)


def build_index_documents(
    sources: list[SourceDocument], extractions: list[dict[str, Any]], node_name: str
) -> list[IndexDocument]:
    """Validate `extractions` (one entry per source, 1-based `index`) and zip
    each with its matching `SourceDocument` into an `IndexDocument`.

    Each extraction entry must have: `index` (int, 1-based, unique, exactly
    covering `1..len(sources)`), `problem_type`/`methods_used`/
    `dataset_characteristics` (each a `list[str]`, empty allowed),
    `key_findings` (`str`, empty allowed), `relevance_score` (`int`/`float`,
    not `bool`, in `[0.0, 1.0]`). Raises `ValueError` naming `node_name` on
    any violation. Returns `[]` when `sources` is empty (no-op, mirrors
    `RagStore.index`'s own empty-list no-op) — `id` is left to
    `IndexDocument`'s own `uuid4` default factory.
    """
    if not sources:
        return []

    indices = [_validate_extraction_index(entry, node_name) for entry in extractions]
    _validate_indices_cover_sources(indices, len(sources), node_name)

    documents_by_index: dict[int, IndexDocument] = {}
    for entry, index in zip(extractions, indices, strict=True):
        source = sources[index - 1]
        documents_by_index[index] = IndexDocument(
            text=f"{source.title}\n\n{source.text}",
            source=source.url,
            problem_type=_validate_str_list(entry.get("problem_type"), "problem_type", node_name),
            methods_used=_validate_str_list(entry.get("methods_used"), "methods_used", node_name),
            dataset_characteristics=_validate_str_list(
                entry.get("dataset_characteristics"), "dataset_characteristics", node_name
            ),
            key_findings=_validate_key_findings(entry.get("key_findings"), node_name),
            relevance_score=_validate_relevance_score(entry.get("relevance_score"), node_name),
        )

    return [documents_by_index[index] for index in sorted(documents_by_index)]


def render_report_markdown(
    title: str, sources: list[SourceDocument], documents: list[IndexDocument]
) -> str:
    """Human-readable markdown report: `# {title}`, a "Sources indexed (N)"
    section with one subsection per document (title/URL from the paired
    source, key findings/problem_type/methods_used/relevance_score from the
    document), or an explicit "No sources found" line when `documents` is
    empty."""
    lines = [f"# {title}", ""]
    if not documents:
        lines.append("No sources found.")
        return "\n".join(lines).strip() + "\n"

    lines.append(f"## Sources indexed ({len(documents)})")
    lines.append("")
    for source, document in zip(sources, documents, strict=True):
        problem_type = ", ".join(document.problem_type) or "none identified"
        methods_used = ", ".join(document.methods_used) or "none identified"
        lines.extend(
            [
                f"### {source.title}",
                f"- URL: {source.url}",
                f"- Key findings: {document.key_findings}",
                f"- Problem type: {problem_type}",
                f"- Methods used: {methods_used}",
                f"- Relevance score: {document.relevance_score}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"
