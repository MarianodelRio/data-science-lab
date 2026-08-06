---
id: T-008
phase: 1
agent: infra-agent
depends_on: [T-001]
status: done
folders: ["src/tools/", "src/memory/"]
outputs: [RagStore.index, .query with metadata filter, local embeddings, structured extraction schema]
size: M
branch: feature/T-008-rag-tool
pr: "https://github.com/MarianodelRio/data-science-lab/pull/10"
---

## rag tool + Chroma + embeddings (src/tools/rag.py, src/memory/store.py)

**Scope:** `src/tools/rag.py` + `src/memory/store.py`.

**Delivers:**
- `RagStore(competition_name, chroma_host, chroma_port)` — one Chroma collection per competition
- Local embeddings via `sentence-transformers` (`nomic-embed-text` or `all-MiniLM-L6-v2`), no external API
- `index(documents: list[Document])` where each Document carries the structured metadata from `design.md` § RAG (`source, problem_type[], methods_used[], dataset_characteristics[], key_findings, relevance_score`)
- `query(text, where: dict | None, n_results) -> list[Document]` supporting metadata filters
- `IndexDocument` dataclass defining the metadata schema (the structured-extraction contract used by research nodes)

**Done when:**
- [x] `index()` then `query("xgboost imbalance")` returns the indexed doc (against a local/ephemeral Chroma client)
- [x] `query(..., where={"problem_type": {"$in": ["binary_classification"]}})` filters correctly
- [x] embeddings run locally with no network call
- [x] two different `competition_name` values use isolated collections
- [x] `mypy src/tools/rag.py src/memory/store.py` passes
- [x] tests cover: index+query, metadata filter, collection isolation
- [x] `docs/pipeline.md` "RAG" section updated

## Completed

Implemented the RAG tool split across two files per the folder split in `folders:`:

- `src/memory/store.py` — low-level Chroma plumbing: `IndexDocument` (frozen dataclass:
  `text, source, problem_type: list[str], methods_used: list[str],
  dataset_characteristics: list[str], key_findings: str, relevance_score: float, id:
  str = uuid4()`), `sanitize_collection_name()` (`rag_{slug}`, invalid chars → `_`,
  clamped to 63 chars, `ValueError` if the sanitized suffix is empty),
  `build_client(host, port)` (`HttpClient` if both given, else `EphemeralClient()`),
  `build_embedding_function()` (`SentenceTransformerEmbeddingFunction("all-MiniLM-L6-v2")`),
  `LIST_VALUED_METADATA_FIELDS`, and `translate_where()`.
- `src/tools/rag.py` — `RagStore(competition_name, chroma_host=None, chroma_port=None, *,
  client=None)`, `.index(documents: list[IndexDocument]) -> None` (upserts, no-op on empty
  list), `.query(text, where=None, n_results=10) -> list[IndexDocument]`. Re-exports
  `IndexDocument` so callers only need `from src.tools.rag import RagStore, IndexDocument`.
- `tests/tools/test_rag.py` — 17 tests, no mocking of embeddings/network, all against
  `chromadb.EphemeralClient()`: index+query round trip, `$in` metadata filtering, collection
  isolation (two `RagStore`s sharing one client), real similarity ordering (`n_results=1`
  excludes an unrelated doc), empty-list no-op, empty/never-indexed collection returns `[]`,
  no-host/port ephemeral fallback, and unit tests for `sanitize_collection_name`/
  `translate_where` in isolation. Full suite: 196 passed (196 total incl. pre-existing).
  `mypy src/tools/rag.py src/memory/store.py`: no issues. `ruff check`/`ruff format --check`:
  clean.

**Key design decisions** (also logged in `context/decisions.md`, two entries):
1. `RagStore`/`store.py` has zero LLM imports — pure storage/embedding/retrieval, per CLAUDE.md
   invariant #8 and design.md classifying `rag` as a Tool. Metadata extraction is the caller's
   job (T-017 `literature_researcher` and future callers construct `IndexDocument` themselves).
2. `translate_where()` rewrites `{"field": {"$in": [...]}}` into `$or`-of-`$contains` for the
   three list-valued metadata fields, because the installed `chromadb==1.5.9` does not match
   `$in` against list-valued metadata (verified empirically) — only `$contains` (membership)
   does. This keeps the caller-facing `where={"problem_type": {"$in": [...]}}` shape from the
   task/design.md while storage stays true `list[str]`.
3. A second, narrower transform — `_legalize_where()` in `src/tools/rag.py` (not part of
   `translate_where`'s own contract/unit tests) — collapses a single-clause `$or`/`$and` list
   down to that one clause immediately before the actual `.query()` call. This was needed
   because the installed Chroma rejects `$and`/`$or` with fewer than two sub-expressions, but
   `translate_where`'s own required unit-test contract (a single `$in` value → a *single-clause*
   `$or`) would otherwise produce an invalid `where` for exactly the single-value case exercised
   by the task's done-when checklist. Verified against real Chroma with a passing end-to-end
   test (`test_query_with_in_filter_matches_only_matching_docs`).
4. Embeddings are wired through Chroma's own `embedding_function=` on
   `get_or_create_collection` (not manual embed-then-pass-vectors), so `.upsert(documents=...)`
   and `.query(query_texts=...)` embed through the same code path and stay consistent.
   `.upsert()` (not `.add()`) is used so re-indexing the same `id` doesn't error.
5. `client:` is a keyword-only escape hatch on `RagStore.__init__` beyond the task's literal
   3-positional-arg signature, letting two `RagStore` instances share one
   `chromadb.EphemeralClient()` to prove real collection isolation in a test (mirrors how
   production `HttpClient` isolation would behave against one Chroma server).

**Two `mypy`-driven fixes worth noting:** `get_or_create_collection(embedding_function=...)`
needed a `# type: ignore[arg-type]` — Chroma's own type stubs for
`SentenceTransformerEmbeddingFunction` don't structurally satisfy the `EmbeddingFunction`
protocol they define (numpy dtype-width mismatch in the stubs, not a real runtime issue,
verified by the tests actually calling it). `zip(ids, documents, metadatas)` in
`RagStore.query()` needed `strict=True` (ruff `B905`) since the three lists always come from
the same Chroma result and are guaranteed equal length.

**Deferred / not in scope**, logged as an OPEN discovery in `context/discoveries.md` for
whoever builds `docker/`/CI for the `chroma` service: `all-MiniLM-L6-v2` downloads from the HF
Hub on first use if not cached locally — a one-time network dependency, not per-call, but a
fresh container/CI runner with no persisted cache and no network access will fail on the first
`RagStore(...)` construction. No Docker/CI files were touched, per this task's folder scope.

## Review fixes (round 1)

Closed 2 BLOCKERs + 4 WARNINGs from code-quality/security/adversarial/smoke-test review, all
independently reproduced against a live `chromadb.EphemeralClient()`:

- **BLOCKER 1 (cross-tenant data leak)**: `sanitize_collection_name` previously collided —
  `"foo bar"`/`"foo_bar"` (space vs. literal `_`) and `"comp!"`/`"comp"` (trailing-char
  stripping after invalid-char replacement) both sanitized to the same collection name, so two
  different competitions could silently share one Chroma collection and read each other's
  indexed documents. Fixed by making the collection name `rag_{readable}_{digest}`, where
  `digest` is a 16-hex-char `sha256(competition_name)` prefix (deterministic,
  effectively-injective — the actual uniqueness guarantee) and `readable` is now purely a
  cosmetic, non-authoritative debug aid. Added
  `test_distinct_names_that_sanitize_to_the_same_readable_part_do_not_collide` proving both
  original collision examples now produce different names, plus a length-bound test.
- **BLOCKER 2 (silent wrong query results)**: `translate_where` used `dict[key] = ...` when
  writing a literal `$and`/`$or` clause from the input `where`, which overwrote (rather than
  merged with) any `$or` clauses already accumulated from translating an `$in` filter on a
  list-valued field in the same `where` dict — so `query(where={"problem_type": {"$in": [...]}},
  "$or": [...]})` silently dropped the `problem_type` condition and returned too-permissive
  results, with no error. Rewrote `translate_where` to never mutate a shared `$or`/`$and` key in
  place: every top-level input key becomes its own single-key clause, and all clauses are
  combined under one `$and` when there's more than one. Added an end-to-end regression test
  (`test_query_combines_in_filter_and_literal_or_with_and_not_overwrite`, via a real
  `RagStore.query()` call against 3 indexed docs) plus a `translate_where`-level unit test,
  both proving both conditions are enforced together.
- **WARNING (a)** `$in` value not validated as a list (e.g. `{"$in": "not_a_list"}` iterated
  char-by-char) — now raises `ValueError` from `translate_where`, covered by
  `test_in_value_not_a_list_raises_value_error`.
- **WARNING (b)** empty `$in` list previously propagated an opaque Chroma `ValueError` — now
  raises a clear `ValueError` naming the field, covered by
  `test_in_value_empty_list_raises_clear_value_error`.
- **WARNING (c)** duplicate `.id` within one `index()` call previously raised an unhandled
  `chromadb.errors.DuplicateIDError` and wrote nothing in the batch (not even the non-duplicate
  docs) — `RagStore.index()` now raises `ValueError` naming the duplicate id(s) before calling
  Chroma at all. Covered by `test_index_raises_on_duplicate_id_and_writes_nothing`.
- **WARNING (d)** `n_results <= 0` previously raised an unhandled Chroma `TypeError` — `RagStore
  .query()` now raises `ValueError(f"n_results must be positive, got {n_results}")` up front.
  Covered by `test_query_rejects_non_positive_n_results`.

Also updated the two pre-existing `sanitize_collection_name` tests (`test_normal_slug`,
`test_slug_with_hyphens`) to assert on the new `rag_{readable}_{digest}` shape instead of an
exact lossy-sanitized string.

Full suite after fixes: 204 passed (25 in `tests/tools/test_rag.py`, up from 17).
`mypy src/tools/rag.py src/memory/store.py`: no issues. `ruff check .` / `ruff format --check
.`: clean.

## Review fixes (round 2)

One more real bug found on independent re-verification: `IndexDocument` with any empty
list-valued field (`problem_type`/`methods_used`/`dataset_characteristics`) crashed
`RagStore.index()` with an unhandled `ValueError` from deep inside Chroma's own metadata
validation (`Expected metadata list value for key '...' to be non-empty`) — Chroma's metadata
schema rejects empty-list values outright, and nothing in `IndexDocument`'s schema or
`.index()` guarded against a document genuinely having zero known values for one of these
fields (e.g. before an LLM extraction pass narrows them down). Fixed by extracting the
metadata-flattening step in `RagStore.index()` into `RagStore._flatten_metadata()`, which now
omits a list-valued field from the Chroma metadata dict entirely when it's empty, rather than
writing `[]`. `.query()`'s reconstruction already used `metadata.get(field, [])` (unchanged),
so a missing key correctly round-trips back to `[]`, and `translate_where`'s `$contains`-based
`$in` filtering on a field that's simply absent from metadata correctly never matches —
semantically identical to "this document has no values for this field," which is the correct
behavior either way, no extra query-side handling needed.

Added `test_index_and_query_round_trip_empty_list_valued_fields` (indexes a doc with
`methods_used=[]`/`dataset_characteristics=[]`, asserts `.index()` doesn't raise and `.query()`
round-trips both back as `[]`) and `test_query_in_filter_excludes_doc_with_empty_list_valued_field`
(a `where={"methods_used": {"$in": [...]}}` query correctly excludes a doc whose
`methods_used` is empty, no crash).

Full suite after this fix: 206 passed (27 in `tests/tools/test_rag.py`). `mypy
src/tools/rag.py src/memory/store.py`: no issues. `ruff check .` / `ruff format --check .`:
clean.
