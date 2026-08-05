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


def test_query_combines_in_filter_and_literal_or_with_and_not_overwrite() -> None:
    """Regression test for a review-found BLOCKER: translate_where used to
    let a literal `$or` key silently overwrite the `$or` clauses generated
    from an `$in` translation, so a `problem_type` filter was dropped
    entirely and only the literal `$or` condition was applied — returning
    too-permissive, wrong results with no error. Both conditions must now
    be enforced (ANDed together), end-to-end against a real query().
    """
    store = RagStore(competition_name="comp-combined-filter")
    matches_both = _make_doc(
        "Binary classification document from source B.",
        source="src-B",
        problem_type=["binary_classification"],
    )
    matches_source_only = _make_doc(
        "Multiclass document from source B.",
        source="src-B",
        problem_type=["multiclass_classification"],
    )
    matches_problem_type_only = _make_doc(
        "Binary classification document from source A.",
        source="src-A",
        problem_type=["binary_classification"],
    )

    store.index([matches_both, matches_source_only, matches_problem_type_only])
    results = store.query(
        "classification document",
        where={
            "problem_type": {"$in": ["binary_classification"]},
            "$or": [{"source": "src-B"}, {"source": "src-C"}],
        },
        n_results=10,
    )

    result_ids = {result.id for result in results}
    assert result_ids == {matches_both.id}


def test_index_raises_on_duplicate_id_and_writes_nothing() -> None:
    store = RagStore(competition_name="comp-duplicate-id")
    doc_a = _make_doc("First document with a shared id.")
    doc_b = _make_doc("Second document with the same shared id.")
    object.__setattr__(doc_b, "id", doc_a.id)

    with pytest.raises(ValueError, match="Duplicate IndexDocument id"):
        store.index([doc_a, doc_b])

    assert store.query("shared id") == []


def test_query_rejects_non_positive_n_results() -> None:
    store = RagStore(competition_name="comp-bad-n-results")
    store.index([_make_doc("Some document.")])

    with pytest.raises(ValueError, match="n_results must be positive"):
        store.query("some document", n_results=0)

    with pytest.raises(ValueError, match="n_results must be positive"):
        store.query("some document", n_results=-5)


class TestSanitizeCollectionName:
    def test_normal_slug(self) -> None:
        result = sanitize_collection_name("titanic")
        assert result.startswith("rag_titanic_")
        # deterministic: same input always sanitizes to the same name.
        assert result == sanitize_collection_name("titanic")

    def test_slug_with_hyphens(self) -> None:
        result = sanitize_collection_name("house-prices-advanced")
        assert result.startswith("rag_house-prices-advanced_")

    def test_slug_requiring_char_replacement(self) -> None:
        result = sanitize_collection_name("Some Comp!!")
        assert result.startswith("rag_")
        assert all(c.isalnum() or c in "_-" for c in result)
        assert not result.endswith("_")
        assert not result.endswith("-")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid competition_name"):
            sanitize_collection_name("")

    def test_distinct_names_that_sanitize_to_the_same_readable_part_do_not_collide(self) -> None:
        """Regression test for a review-found BLOCKER: lossy character
        replacement made "foo bar" (space) and "foo_bar" (literal
        underscore) sanitize to the same collection name, and "comp!" and
        "comp" also collided after trailing-char stripping — silently
        merging two different competitions' RAG collections into one
        (a cross-tenant data leak). Collection names must now be a
        deterministic, effectively-injective function of the raw input.
        """
        assert sanitize_collection_name("foo bar") != sanitize_collection_name("foo_bar")
        assert sanitize_collection_name("comp!") != sanitize_collection_name("comp")

    def test_collection_name_within_chroma_length_bounds(self) -> None:
        name = sanitize_collection_name("a" * 200)
        assert 3 <= len(name) <= 63


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

    def test_in_value_not_a_list_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match=r"\$in value must be a list"):
            translate_where({"problem_type": {"$in": "not_a_list"}})

    def test_in_value_empty_list_raises_clear_value_error(self) -> None:
        with pytest.raises(ValueError, match=r"\$in value must not be empty"):
            translate_where({"problem_type": {"$in": []}})

    def test_literal_or_combined_with_in_filter_does_not_overwrite(self) -> None:
        """Regression test for the BLOCKER: a literal `$or` key used to
        silently overwrite the `$or` clauses generated from translating an
        `$in` field, dropping that filter entirely. Both must now survive,
        combined via `$and`.
        """
        result = translate_where(
            {
                "problem_type": {"$in": ["binary_classification"]},
                "$or": [{"source": "src-B"}, {"source": "src-C"}],
            }
        )

        assert result is not None
        assert "$and" in result
        clauses = result["$and"]
        assert {"$or": [{"problem_type": {"$contains": "binary_classification"}}]} in clauses
        assert {"$or": [{"source": "src-B"}, {"source": "src-C"}]} in clauses
