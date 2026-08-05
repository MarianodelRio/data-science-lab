"""Unit tests for src/tools/rag.py + src/memory/store.py.

All tests run against `chromadb.EphemeralClient()` (in-memory, no Docker).
No mocking of embeddings or network primitives — the local
`sentence-transformers` model does the real embedding work (downloaded from
HF Hub on first use only; steady-state runs are network-free once cached).
"""

from __future__ import annotations

import chromadb
import pytest

from src.memory.store import sanitize_collection_name, translate_where
from src.tools.rag import IndexDocument, RagStore


def _make_doc(
    text: str,
    *,
    source: str = "arxiv",
    problem_type: list[str] | None = None,
    methods_used: list[str] | None = None,
    dataset_characteristics: list[str] | None = None,
    key_findings: str = "some findings",
    relevance_score: float = 0.9,
) -> IndexDocument:
    return IndexDocument(
        text=text,
        source=source,
        problem_type=problem_type if problem_type is not None else ["binary_classification"],
        methods_used=methods_used if methods_used is not None else ["xgboost"],
        dataset_characteristics=(
            dataset_characteristics if dataset_characteristics is not None else ["imbalanced"]
        ),
        key_findings=key_findings,
        relevance_score=relevance_score,
    )


def test_index_then_query_returns_indexed_doc() -> None:
    store = RagStore(competition_name="comp-index-query")
    doc = _make_doc("Using XGBoost to handle severe class imbalance in fraud detection.")

    store.index([doc])
    results = store.query("xgboost imbalance")

    assert any(result.id == doc.id for result in results)


def test_query_with_in_filter_matches_only_matching_docs() -> None:
    store = RagStore(competition_name="comp-metadata-filter")
    matching = _make_doc(
        "Binary classification with gradient boosting.",
        problem_type=["binary_classification"],
    )
    other = _make_doc(
        "Multi-class classification with neural networks.",
        problem_type=["multiclass_classification"],
    )

    store.index([matching, other])
    results = store.query(
        "classification",
        where={"problem_type": {"$in": ["binary_classification"]}},
        n_results=10,
    )

    result_ids = {result.id for result in results}
    assert matching.id in result_ids
    assert other.id not in result_ids


def test_two_competitions_use_isolated_collections() -> None:
    shared_client = chromadb.EphemeralClient()
    store_a = RagStore(competition_name="comp-a", client=shared_client)
    store_b = RagStore(competition_name="comp-b", client=shared_client)

    doc = _make_doc("Only indexed into competition A.")
    store_a.index([doc])

    results_a = store_a.query("indexed into competition A")
    results_b = store_b.query("indexed into competition A")

    assert any(result.id == doc.id for result in results_a)
    assert all(result.id != doc.id for result in results_b)
    assert results_b == []


def test_query_similarity_ordering_excludes_unrelated_doc_with_top1() -> None:
    store = RagStore(competition_name="comp-similarity")
    relevant = _make_doc("LightGBM gradient boosting hyperparameter tuning for tabular regression.")
    unrelated = _make_doc(
        "A recipe for baking sourdough bread with a poolish starter.",
        problem_type=["other"],
        methods_used=["baking"],
        dataset_characteristics=["n/a"],
    )

    store.index([relevant, unrelated])
    results = store.query("gradient boosting hyperparameter tuning", n_results=1)

    assert len(results) == 1
    assert results[0].id == relevant.id


def test_index_empty_list_is_noop() -> None:
    store = RagStore(competition_name="comp-empty-index")

    store.index([])

    assert store.query("anything") == []


def test_ragstore_sanitizes_unusual_competition_name_and_is_usable() -> None:
    store = RagStore(competition_name="Some Comp!!")
    doc = _make_doc("A document indexed through a sanitized collection name.")

    store.index([doc])
    results = store.query("sanitized collection name")

    assert any(result.id == doc.id for result in results)


def test_query_on_never_indexed_collection_returns_empty_list() -> None:
    store = RagStore(competition_name="comp-never-indexed")

    results = store.query("anything at all")

    assert results == []


def test_ragstore_with_no_host_port_uses_ephemeral_client() -> None:
    store = RagStore(competition_name="comp-no-host-port")

    # No exception on construction, and it behaves like a working store.
    doc = _make_doc("Ephemeral fallback works with no host/port args.")
    store.index([doc])
    results = store.query("ephemeral fallback")

    assert any(result.id == doc.id for result in results)


class TestSanitizeCollectionName:
    def test_normal_slug(self) -> None:
        assert sanitize_collection_name("titanic") == "rag_titanic"

    def test_slug_with_hyphens(self) -> None:
        assert sanitize_collection_name("house-prices-advanced") == "rag_house-prices-advanced"

    def test_slug_requiring_char_replacement(self) -> None:
        result = sanitize_collection_name("Some Comp!!")
        assert result.startswith("rag_")
        assert all(c.isalnum() or c in "_-" for c in result)
        assert not result.endswith("_")
        assert not result.endswith("-")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid competition_name"):
            sanitize_collection_name("")


class TestTranslateWhere:
    def test_single_value_in_translates_to_or_contains(self) -> None:
        result = translate_where({"problem_type": {"$in": ["a"]}})
        assert result == {"$or": [{"problem_type": {"$contains": "a"}}]}

    def test_multi_value_in_translates_to_multi_clause_or(self) -> None:
        result = translate_where({"problem_type": {"$in": ["a", "b"]}})
        assert result == {
            "$or": [
                {"problem_type": {"$contains": "a"}},
                {"problem_type": {"$contains": "b"}},
            ]
        }

    def test_non_list_field_passes_through_unchanged(self) -> None:
        assert translate_where({"source": "arxiv"}) == {"source": "arxiv"}

    def test_none_passes_through_as_none(self) -> None:
        assert translate_where(None) is None

    def test_nested_inside_and_is_still_translated(self) -> None:
        result = translate_where(
            {
                "$and": [
                    {"source": "arxiv"},
                    {"problem_type": {"$in": ["a"]}},
                ]
            }
        )
        assert result == {
            "$and": [
                {"source": "arxiv"},
                {"$or": [{"problem_type": {"$contains": "a"}}]},
            ]
        }
