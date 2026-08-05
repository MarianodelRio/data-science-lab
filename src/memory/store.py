"""Low-level ChromaDB wrapper: client construction, collection-name
sanitization, the local embedding function, and the IndexDocument schema.

No LLM calls anywhere in this module — metadata extraction is the caller's
job (see src/tools/rag.py). This module only knows how to talk to Chroma
and how to shape/translate metadata filters.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

LIST_VALUED_METADATA_FIELDS = frozenset({"problem_type", "methods_used", "dataset_characteristics"})

_MAX_COLLECTION_NAME_LEN = 63
_COLLECTION_NAME_PREFIX = "rag_"
_READABLE_PART_MAX_LEN = 30
_HASH_DIGEST_LEN = 16
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
    """Build a Chroma-safe, collision-proof collection name from
    `competition_name`.

    The name is `rag_{readable}_{digest}`: `digest` is a 16-hex-char prefix
    of `sha256(competition_name)` — a deterministic, effectively-injective
    function of the raw input, so two distinct `competition_name` values can
    never collide on collection name. `readable` is a best-effort, purely
    cosmetic slug (invalid chars replaced by `_`, trailing `_`/`-` stripped,
    clamped to 30 chars, falling back to `"x"` if it sanitizes to nothing) —
    it exists only for human debuggability in the Chroma admin UI/CLI and
    plays no role in uniqueness. Raises `ValueError` if `competition_name` is
    empty.
    """
    if not competition_name:
        raise ValueError(f"Invalid competition_name: {competition_name!r}")

    digest = hashlib.sha256(competition_name.encode("utf-8")).hexdigest()[:_HASH_DIGEST_LEN]

    readable = _INVALID_CHARS_RE.sub("_", competition_name)[:_READABLE_PART_MAX_LEN]
    readable = _TRAILING_NON_ALNUM_RE.sub("", readable) or "x"

    return f"{_COLLECTION_NAME_PREFIX}{readable}_{digest}"


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

    Every top-level key of the input `where` becomes its own single-key
    clause, and all clauses (translated `$in` clauses and everything else)
    are combined under one `$and` when there is more than one — never
    merged into a single multi-key dict and never allowed to silently
    overwrite one another (in particular, a literal `$or`/`$and` key in the
    input can never clobber an `$in`-derived `$or` clause, or vice versa,
    even when both are present in the same `where`).
    """
    if where is None:
        return None

    clauses: list[dict[str, Any]] = []
    for key, value in where.items():
        if key in ("$and", "$or") and isinstance(value, list):
            translated_list = [
                translated_clause
                for clause in value
                if (translated_clause := translate_where(clause)) is not None
            ]
            clauses.append({key: translated_list})
            continue

        if key in LIST_VALUED_METADATA_FIELDS and isinstance(value, dict) and "$in" in value:
            in_values = value["$in"]
            if not isinstance(in_values, list):
                raise ValueError(f"$in value must be a list, got {in_values!r} for field {key!r}")
            if not in_values:
                raise ValueError(f"$in value must not be empty for field {key!r}")
            clauses.append({"$or": [{key: {"$contains": member}} for member in in_values]})
            continue

        clauses.append({key: value})

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}
