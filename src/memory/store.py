"""Low-level ChromaDB wrapper: client construction, collection-name
sanitization, the local embedding function, and the IndexDocument schema.

No LLM calls anywhere in this module — metadata extraction is the caller's
job (see src/tools/rag.py). This module only knows how to talk to Chroma
and how to shape/translate metadata filters.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

LIST_VALUED_METADATA_FIELDS = frozenset({"problem_type", "methods_used", "dataset_characteristics"})

_MAX_COLLECTION_NAME_LEN = 63
_MIN_COLLECTION_NAME_LEN = 3
_COLLECTION_NAME_PREFIX = "rag_"
_INVALID_CHARS_RE = re.compile(r"[^a-zA-Z0-9_-]")
_TRAILING_NON_ALNUM_RE = re.compile(r"[_-]+$")


@dataclass(frozen=True)
class IndexDocument:
    """One retrievable unit in the RAG store, plus the structured metadata
    from design.md § RAG. `text` is what gets embedded; everything else is
    metadata attached to it in Chroma.
    """

    text: str
    source: str
    problem_type: list[str]
    methods_used: list[str]
    dataset_characteristics: list[str]
    key_findings: str
    relevance_score: float
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


def sanitize_collection_name(competition_name: str) -> str:
    """Build a Chroma-safe collection name from `competition_name`.

    `f"rag_{competition_name}"`, with any character outside
    `[a-zA-Z0-9_-]` replaced by `_`, trailing `_`/`-` runs stripped, and the
    result clamped to 63 characters (truncating the competition-name portion,
    never the `rag_` prefix). Raises `ValueError` if the sanitized name would
    be shorter than 3 characters.
    """
    sanitized_suffix = _INVALID_CHARS_RE.sub("_", competition_name)
    max_suffix_len = _MAX_COLLECTION_NAME_LEN - len(_COLLECTION_NAME_PREFIX)
    sanitized_suffix = sanitized_suffix[:max_suffix_len]
    sanitized_suffix = _TRAILING_NON_ALNUM_RE.sub("", sanitized_suffix)

    # The "rag_" prefix alone is already _MIN_COLLECTION_NAME_LEN long, so the
    # only way the final name can be too short is an empty sanitized suffix.
    if not sanitized_suffix:
        raise ValueError(f"Invalid competition_name: {competition_name!r}")

    return f"{_COLLECTION_NAME_PREFIX}{sanitized_suffix}"


def build_client(host: str | None, port: int | None) -> chromadb.ClientAPI:
    """Build a Chroma client.

    `host` AND `port` both given -> `chromadb.HttpClient` (the Docker
    `chroma` service). Either omitted -> `chromadb.EphemeralClient()`
    (in-memory, no network, used by tests).
    """
    if host is not None and port is not None:
        return chromadb.HttpClient(host=host, port=port)
    return chromadb.EphemeralClient()


def build_embedding_function() -> embedding_functions.SentenceTransformerEmbeddingFunction:
    """Local sentence-transformers embedding function (no external API)."""
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=_EMBEDDING_MODEL_NAME
    )


def translate_where(where: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rewrite `{field: {"$in": [...]}}` into an `$or`-of-`$contains` clause
    for any `field` in `LIST_VALUED_METADATA_FIELDS`, since Chroma's `$in`
    does not match list-valued metadata directly (only `$contains`, a
    membership check, does). Recurses into `$and`/`$or` clause lists.
    Everything else (including `None`) passes through unchanged.
    """
    if where is None:
        return None

    translated: dict[str, Any] = {}
    for key, value in where.items():
        if key in ("$and", "$or") and isinstance(value, list):
            translated[key] = [
                translated_clause
                for clause in value
                if (translated_clause := translate_where(clause)) is not None
            ]
            continue

        if key in LIST_VALUED_METADATA_FIELDS and isinstance(value, dict) and "$in" in value:
            translated.setdefault("$or", [])
            translated["$or"].extend({key: {"$contains": member}} for member in value["$in"])
            continue

        translated[key] = value

    return translated
