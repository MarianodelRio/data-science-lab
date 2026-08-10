---
id: T-019
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-008]
status: done
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [memory_manager node, RAG deduplication + consolidation]
size: S
branch: feature/T-019-node-memory-manager
pr: "https://github.com/MarianodelRio/data-science-lab/pull/21"
---

## Node: memory_manager (Pipeline Phase 2)

**Scope:** `memory_manager` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Consolidates the Chroma collection after research: deduplicates near-identical entries, re-scores relevance
- Exposes the RAG query used by later phases ("what did we already try that failed?")
- `model_role: fast`

**Done when:**
- [x] given a RagStore with two near-duplicate docs, the node reduces them to one (mocked embeddings/similarity)
- [x] a query returns the consolidated set
- [x] agent YAML + prompt v1 exist and load
- [x] unit test uses a fake RagStore, no network
- [x] `docs/agents.md` row added

## Completed

Implemented `memory_manager` as a real `LLMNode` (`src/nodes/llm/memory_manager.py`), registered
via `config/agents/memory_manager.yaml` (`model_role: fast`, `output_file_pattern:
"reports/memory_consolidation.md"`) and `config/prompts/memory_manager/v1.md`. It runs last in
`config/phases/phase2_research.yaml`'s `sequence` (already listed there — no phase-config edit
needed), after `literature_researcher`/`web_researcher`/`competition_analyst` have indexed their
findings.

**Scope caveat — stated explicitly, not buried:** `RagStore` (`src/tools/rag.py`, a protected
contract) only exposes `index()` (upsert-by-id) and `query()` (similarity search) — no
list-all/delete API. So "deduplicates near-identical entries... reduces them to one" is implemented
as **query-window consolidation**, not a corpus-wide scan/delete: the node queries a representative
window of candidates (`_QUERY_N_RESULTS = 20`), has the LLM cluster near-duplicates within that
window, and merges each cluster into one consolidated `IndexDocument` re-indexed under a reused
`.id` (the canonical member's original id — upsert collapses that row). **Non-canonical sibling
rows are NOT physically deleted** (no `delete()` exists on `RagStore`) — they remain stale in
Chroma until a future `RagStore`/`IndexDocument` enhancement adds deterministic ids and/or delete
support (see `context/discoveries.md`'s T-017 OPEN entry, cross-referenced in
`context/decisions.md`'s new T-019 entry rather than duplicated). A near-duplicate the query window
never surfaces is also left untouched by a given pass. This was a human-approved scope adjustment
(Architect + user sign-off) against the task file's more literal wording above.

No separate "query memory" helper was added: `RagStore.query()` is already public/importable, so
any future node wanting "what did we already try" instantiates its own `RagStore` and calls
`.query()` directly, identical to how `memory_manager` itself does it via `_ensure_rag_store`.

Added `tests/unit/nodes/llm/test_memory_manager.py` (12 tests, no network — `RagStore` is doubled
via an in-memory `FakeRagStore`) covering: two-near-duplicates-consolidate-to-one, a subsequent
query reflecting the merged fields under the canonical id (documenting the stale-sibling-row
caveat rather than asserting a literal total-count of 1, since the given `FakeRagStore` — which
mirrors real `RagStore` semantics exactly, including "no delete" — cannot honestly produce a
single-result query after merging two originally-distinct ids), no-duplicates-found reindexing
each candidate unchanged, zero-candidates no-op, malformed-JSON / non-partition cluster
`ValueError`s naming `memory_manager`, the `{"messages"}`-only state-delta regression guard, real
config/prompt loading, zero-arg construction, and the `_build_query`/`_read_problem_type`
duplicated-logic tests mirrored from `literature_researcher`'s own test shape. Added a `docs/agents.md`
row and a `context/decisions.md` entry documenting the scope choice and cross-referencing the T-017
discovery.

Verification: `ruff check . && ruff format --check .` clean; `mypy src/` clean (53 files, no
issues); `pytest tests/unit/nodes/llm/test_memory_manager.py` 12/12 passed; `pytest tests/unit -q`
317 passed, 1 pre-existing failure (`tests/unit/graph/test_checkpointer.py::
test_resume_after_restart_does_not_rerun_completed_phase`) confirmed present on `origin/main`
before this task's changes (both in isolation and as part of the full suite) — unrelated to
`memory_manager` and outside this task's `folders:`. Logged as a new `context/discoveries.md`
OPEN entry for whoever owns `src/graph/` checkpointing.

## Review round follow-up

Four parallel reviewers (code-quality, security, smoke-tester, adversarial) ran against the PR
diff — no BLOCKER from any of them. Two non-blocking findings were fixed before merge rather than
shipped as warnings:

- **Missing negative-path test coverage** for the local validators (`_validate_relevance_score_field`,
  `_validate_str_list_field`, `_validate_cluster_indices`) — flagged by both security and adversarial
  as the same risk class as a real bug T-018 shipped once (an unbounded `relevance_score` reaching
  the RAG store, caught only by review). Logic was already correct; added tests for out-of-range
  score, `bool`-as-score, non-numeric score, non-string list items, and unsorted cluster `indices`
  (proving canonical-id selection picks the lowest original index).
- **`_build_query`/`_read_problem_type` duplication hit its own documented threshold**: byte-for-byte
  identical across `literature_researcher`, `web_researcher`, and now `memory_manager` (the third
  occurrence the 2026-08-07 T-017 decision explicitly named as the point to extract). Hoisted both
  into `_research_common.py` as `build_ml_techniques_query`/`read_problem_type`; all three node
  files now import and call the shared functions.

Final state: `tests/unit/nodes/llm/test_memory_manager.py` grew to 21 tests; `pytest tests/unit -q`
345 passed (only the pre-existing checkpointer failure remains); `memory_manager.py` coverage 96%.
See `context/decisions.md`'s `## 2026-08-10 — T-019 [pipeline-agent] (post-review follow-up)` entry.
