"""Tool-facing RAG API: one Chroma collection per competition, local
embeddings, metadata-filtered retrieval.

Pure storage/embedding/retrieval — no LLM calls in this module (CLAUDE.md
invariant #8; design.md classifies `rag` as a Tool, not an LLM node).
Structured-metadata extraction from raw documents happens in the calling
node (e.g. T-017 literature_researcher), which constructs IndexDocument
objects itself and passes them to RagStore.index().
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import chromadb

from src.memory.store import (
    IndexDocument,
    build_client,
    build_embedding_function,
    sanitize_collection_name,
    translate_where,
)

__all__ = ["RagStore", "IndexDocument"]


def _legalize_where(where: dict[str, Any] | None) -> dict[str, Any] | None:
    """Collapse single-clause `$and`/`$or` lists produced by `translate_where`
    (e.g. a single-value `$in` on a list-valued field) down to that one
    clause. The installed Chroma version rejects `$and`/`$or` with fewer than
    two sub-expressions, but `translate_where`'s own contract (and its unit
    tests) always wraps translated list-field filters in `$or`, even for a
    single value — this is the boundary where that gets made Chroma-legal
    before the actual `.query()` call. Recurses so nested single-clause
    operators are collapsed too.
    """
    if where is None:
        return None

    if len(where) == 1:
        ((key, value),) = where.items()
        if key in ("$and", "$or") and isinstance(value, list):
            legalized_clauses = [_legalize_where(raw_clause) for raw_clause in value]
            if len(legalized_clauses) == 1:
                return legalized_clauses[0]
            return {key: legalized_clauses}

    return where


class RagStore:
    """One Chroma collection per competition, with local embeddings and
    metadata-filtered retrieval.
    """

    def __init__(
        self,
        competition_name: str,
        chroma_host: str | None = None,
        chroma_port: int | None = None,
        *,
        client: chromadb.ClientAPI | None = None,
    ) -> None:
        """`chroma_host` AND `chroma_port` both given -> `chromadb.HttpClient`
        (the Docker `chroma` service); either omitted -> `chromadb.EphemeralClient()`
        (in-memory, used by tests). `client` is a keyword-only escape hatch so
        callers/tests can inject an already-built client (e.g. to share one
        Chroma client across two `RagStore` instances and prove collection
        isolation on a single server).
        """
        self._client = client if client is not None else build_client(chroma_host, chroma_port)
        self._collection = self._client.get_or_create_collection(
            name=sanitize_collection_name(competition_name),
            embedding_function=build_embedding_function(),  # type: ignore[arg-type]
        )

    def index(self, documents: list[IndexDocument]) -> None:
        """Embed `documents` locally and upsert into this competition's
        collection. No-op on an empty list (Chroma's `.add()` errors on
        empty batches).

        Raises `ValueError` naming the offending id(s) if `documents`
        contains duplicate `.id` values — Chroma's own
        `DuplicateIDError` for this would otherwise abort the whole batch
        (including the non-duplicate docs) with an opaque error.
        """
        if not documents:
            return

        ids = [doc.id for doc in documents]
        id_counts = Counter(ids)
        duplicate_ids = sorted(doc_id for doc_id, count in id_counts.items() if count > 1)
        if duplicate_ids:
            raise ValueError(
                f"Duplicate IndexDocument id(s) in a single index() call: {duplicate_ids!r}"
            )

        texts = [doc.text for doc in documents]
        metadatas = [self._flatten_metadata(doc) for doc in documents]

        self._collection.upsert(ids=ids, documents=texts, metadatas=metadatas)  # type: ignore[arg-type]

    @staticmethod
    def _flatten_metadata(doc: IndexDocument) -> dict[str, Any]:
        """Flatten an `IndexDocument`'s fields into a Chroma metadata dict.

        List-valued fields (`problem_type`, `methods_used`,
        `dataset_characteristics`) are omitted entirely when empty: Chroma's
        metadata schema rejects empty-list values outright (`ValueError:
        Expected metadata list value for key '...' to be non-empty`), and a
        document with no known values for a field is semantically the same
        as that field being absent — it should never match any `$in`/
        `$contains` filter on that field either way. `.query()`'s
        reconstruction already reads these back via `metadata.get(field,
        [])`, so a missing key round-trips to `[]` correctly.
        """
        metadata: dict[str, Any] = {
            "source": doc.source,
            "key_findings": doc.key_findings,
            "relevance_score": doc.relevance_score,
        }
        for field_name in ("problem_type", "methods_used", "dataset_characteristics"):
            value = getattr(doc, field_name)
            if value:
                metadata[field_name] = value
        return metadata

    def query(
        self,
        text: str,
        where: dict[str, Any] | None = None,
        n_results: int = 10,
    ) -> list[IndexDocument]:
        """Dense-similarity query, embedded locally, with an optional
        metadata filter (translated via `translate_where`). Returns `[]` if
        the collection is empty or nothing matches.

        Raises `ValueError` if `n_results` is not positive (Chroma itself
        raises an opaque `TypeError` for this).
        """
        if n_results <= 0:
            raise ValueError(f"n_results must be positive, got {n_results}")

        results = self._collection.query(
            query_texts=[text],
            n_results=n_results,
            where=_legalize_where(translate_where(where)),
        )

        ids = results["ids"][0]
        if not ids:
            return []

        documents = results["documents"][0] if results["documents"] else [""] * len(ids)
        metadatas = results["metadatas"][0] if results["metadatas"] else [{}] * len(ids)

        return [
            IndexDocument(
                id=doc_id,
                text=doc_text or "",
                source=str(metadata.get("source", "")),
                problem_type=list(metadata.get("problem_type", [])),  # type: ignore[arg-type]
                methods_used=list(metadata.get("methods_used", [])),  # type: ignore[arg-type]
                dataset_characteristics=list(metadata.get("dataset_characteristics", [])),  # type: ignore[arg-type]
                key_findings=str(metadata.get("key_findings", "")),
                relevance_score=float(metadata.get("relevance_score", 0.0)),  # type: ignore[arg-type]
            )
            for doc_id, doc_text, metadata in zip(ids, documents, metadatas, strict=True)
        ]
