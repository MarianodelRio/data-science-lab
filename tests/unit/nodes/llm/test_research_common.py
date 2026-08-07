"""Unit tests for src/nodes/llm/_research_common.py.

No LLM/network calls at all — every function here is pure data
transformation/validation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.memory.store import IndexDocument
from src.nodes.llm._research_common import (
    SourceDocument,
    build_index_documents,
    build_source_context,
    extract_json_array,
    relative_to_workspace,
    render_report_markdown,
)

# -- extract_json_array --


def test_extract_json_array_parses_raw_json() -> None:
    result = extract_json_array('[{"index": 1}]', "literature_researcher")

    assert result == [{"index": 1}]


def test_extract_json_array_parses_single_json_fence() -> None:
    result = extract_json_array('```json\n[{"index": 1}]\n```', "literature_researcher")

    assert result == [{"index": 1}]


def test_extract_json_array_parses_single_unlabeled_fence() -> None:
    result = extract_json_array('```\n[{"index": 1}]\n```', "literature_researcher")

    assert result == [{"index": 1}]


def test_extract_json_array_invalid_json_raises_value_error() -> None:
    with pytest.raises(ValueError, match="literature_researcher"):
        extract_json_array("not json at all {", "literature_researcher")


def test_extract_json_array_non_list_top_level_raises_value_error() -> None:
    with pytest.raises(ValueError, match="literature_researcher"):
        extract_json_array('{"index": 1}', "literature_researcher")


def test_extract_json_array_embedded_backtick_in_string_value_parses() -> None:
    """Regression guard mirroring problem_framer/leakage_auditor: a single
    ```json fence wrapping the whole response must parse correctly even when
    a string value inside the JSON itself contains a literal ``` sequence."""
    content = '```json\n[{"index": 1, "key_findings": "uses ```df.shift(-1)``` internally"}]\n```'

    result = extract_json_array(content, "web_researcher")

    assert result == [{"index": 1, "key_findings": "uses ```df.shift(-1)``` internally"}]


# -- build_index_documents --


def _source(i: int) -> SourceDocument:
    return SourceDocument(title=f"Title {i}", text=f"Text {i}", url=f"https://example.com/{i}")


def _extraction(index: int, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "index": index,
        "problem_type": ["binary_classification"],
        "methods_used": ["gradient_boosting"],
        "dataset_characteristics": ["tabular"],
        "key_findings": f"finding {index}",
        "relevance_score": 0.5,
    }
    base.update(overrides)
    return base


def test_build_index_documents_happy_path_two_sources() -> None:
    sources = [_source(1), _source(2)]
    extractions = [_extraction(1), _extraction(2)]

    documents = build_index_documents(sources, extractions, "literature_researcher")

    assert len(documents) == 2
    assert documents[0].text == "Title 1\n\nText 1"
    assert documents[0].source == "https://example.com/1"
    assert documents[0].problem_type == ["binary_classification"]
    assert documents[0].methods_used == ["gradient_boosting"]
    assert documents[0].dataset_characteristics == ["tabular"]
    assert documents[0].key_findings == "finding 1"
    assert documents[0].relevance_score == 0.5
    assert documents[1].source == "https://example.com/2"


def test_build_index_documents_duplicate_index_raises_value_error() -> None:
    sources = [_source(1), _source(2)]
    extractions = [_extraction(1), _extraction(1)]

    with pytest.raises(ValueError, match="literature_researcher"):
        build_index_documents(sources, extractions, "literature_researcher")


def test_build_index_documents_missing_index_raises_value_error() -> None:
    sources = [_source(1), _source(2)]
    extractions = [_extraction(1)]

    with pytest.raises(ValueError, match="literature_researcher"):
        build_index_documents(sources, extractions, "literature_researcher")


@pytest.mark.parametrize("bad_index", [0, 3])
def test_build_index_documents_out_of_range_index_raises_value_error(bad_index: int) -> None:
    sources = [_source(1), _source(2)]
    extractions = [_extraction(1), _extraction(bad_index)]

    with pytest.raises(ValueError, match="literature_researcher"):
        build_index_documents(sources, extractions, "literature_researcher")


def test_build_index_documents_non_list_problem_type_raises_value_error() -> None:
    sources = [_source(1)]
    extractions = [_extraction(1, problem_type="binary_classification")]

    with pytest.raises(ValueError, match="problem_type"):
        build_index_documents(sources, extractions, "literature_researcher")


def test_build_index_documents_wrong_element_type_in_list_raises_value_error() -> None:
    sources = [_source(1)]
    extractions = [_extraction(1, methods_used=[1, 2])]

    with pytest.raises(ValueError, match="methods_used"):
        build_index_documents(sources, extractions, "literature_researcher")


@pytest.mark.parametrize("bad_score", [-0.1, 1.1])
def test_build_index_documents_relevance_score_out_of_range_raises_value_error(
    bad_score: float,
) -> None:
    sources = [_source(1)]
    extractions = [_extraction(1, relevance_score=bad_score)]

    with pytest.raises(ValueError, match="relevance_score"):
        build_index_documents(sources, extractions, "literature_researcher")


def test_build_index_documents_relevance_score_as_bool_raises_value_error() -> None:
    sources = [_source(1)]
    extractions = [_extraction(1, relevance_score=True)]

    with pytest.raises(ValueError, match="relevance_score"):
        build_index_documents(sources, extractions, "literature_researcher")


def test_build_index_documents_empty_sources_returns_empty_list() -> None:
    assert build_index_documents([], [], "literature_researcher") == []


def test_build_index_documents_empty_extractions_with_sources_raises_value_error() -> None:
    with pytest.raises(ValueError, match="literature_researcher"):
        build_index_documents([_source(1)], [], "literature_researcher")


# -- relative_to_workspace --


def test_relative_to_workspace_passes_through_relative_path() -> None:
    workspace = MagicMock()
    workspace.workspace_path = Path("/workspace")

    assert relative_to_workspace("reports/problem_definition.json", workspace) == (
        "reports/problem_definition.json"
    )


def test_relative_to_workspace_relativizes_absolute_path() -> None:
    workspace = MagicMock()
    workspace.workspace_path = Path("/workspace")

    result = relative_to_workspace("/workspace/reports/problem_definition.json", workspace)

    assert result == "reports/problem_definition.json"


# -- render_report_markdown --


def test_render_report_markdown_contains_title_sources_and_findings() -> None:
    sources = [_source(1)]
    documents = [
        IndexDocument(
            text="Title 1\n\nText 1",
            source="https://example.com/1",
            problem_type=["binary_classification"],
            methods_used=["gradient_boosting"],
            dataset_characteristics=["tabular"],
            key_findings="a useful finding",
            relevance_score=0.8,
        )
    ]

    markdown = render_report_markdown("Literature Research", sources, documents)

    assert "# Literature Research" in markdown
    assert "Title 1" in markdown
    assert "https://example.com/1" in markdown
    assert "a useful finding" in markdown


def test_render_report_markdown_empty_documents_has_no_sources_marker() -> None:
    markdown = render_report_markdown("Literature Research", [], [])

    assert "No sources found" in markdown


# -- build_source_context --


def test_build_source_context_numbers_sources_from_one() -> None:
    sources = [_source(1), _source(2)]

    context = build_source_context(sources)

    assert "### Source 1" in context
    assert "### Source 2" in context
    assert "Title 1" in context
    assert "https://example.com/1" in context
    assert "Text 1" in context
    assert "Title 2" in context


def test_build_source_context_empty_sources_has_no_sources_marker() -> None:
    context = build_source_context([])

    assert "No sources were found" in context
