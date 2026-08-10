---
id: T-018
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-007, T-008]
status: pr-open
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/", "src/tools/kaggle_client.py"]
outputs: [competition_analyst node, Kaggle top-voted-notebook findings indexed to RAG]
size: S
branch: feature/T-018-node-competition-analyst
pr: "https://github.com/MarianodelRio/data-science-lab/pull/20"
---

## Node: competition_analyst (Pipeline Phase 2)

**Scope:** `competition_analyst` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Uses `kaggle_client` to pull competition top-voted notebooks (forum posts dropped — no Kaggle API surface exists, see `context/discoveries.md` 2026-08-07 entry)
- LLM extracts winning patterns (features that matter, model families) into `IndexDocument`s, indexes into `RagStore`
- `model_role: research`

**Done when:**
- [x] with mocked `kaggle_client` + mocked LLM, the node indexes ≥1 structured document into a fake RagStore
- [x] indexed docs include `methods_used` metadata
- [x] agent YAML + prompt v1 exist and load
- [x] unit test mocks kaggle + LLM, no network
- [x] `docs/agents.md` row added

## Completed

**Scope adjustment (human-approved, applied):** "forum posts" was dropped entirely — no Kaggle
API surface exists for it in the installed `kaggle`/`kagglesdk` packages (see
`context/discoveries.md`'s 2026-08-07 T-018 entry). `competition_analyst` extracts winning
patterns from top-voted kernels' **title/author/vote-count metadata only** (never notebook
code/output).

**What was implemented:**
- `src/tools/kaggle_client.py` (additive only, `download`/`submit`/`get_score` untouched):
  - `KaggleApiProtocol.kernels_list(competition=None, sort_by=None, page_size=20) -> list[Any]`
  - `list_top_kernels(competition, n=10, api=None) -> list[dict[str, Any]]` — reuses
    `_validate_competition`/`_default_api`, clamps `page_size` at 100, sorts by `voteCount`,
    returns `{ref, title, author, total_votes, url}` dicts.
  - Tests appended to `tests/tools/test_kaggle_client.py` (`_fake_kernel` helper +
    args/shape/truncation/page-size-clamp/non-positive-`n`/bad-competition/missing-credentials
    cases).
- `src/nodes/llm/competition_analyst.py` (new): `CompetitionAnalystNode(LLMNode)`. Reuses
  `src.nodes.llm._research_common`'s `extract_json_array`/`build_index_documents`/
  `render_report_markdown` — the same shared helpers `literature_researcher.py`/
  `web_researcher.py` (T-017) already use — instead of a local reimplementation (see the
  "Fix pass" note below for why this wasn't the case initially):
  - `KernelSummary` frozen dataclass (`ref`, `title`, `author`, `total_votes`, `url`), plus
    `_to_source_document` adapting one into `_research_common.SourceDocument(title, text, url)`
    (`text` packs author/vote-count) for the shared helpers
  - `_build_kernel_context` — kept local/kernel-specific (`## Kernels` / `### Kernel {i}`
    headings, distinct from `_research_common`'s generic `build_source_context`), matching what
    `config/prompts/competition_analyst/v1.md` documents
  - `__init__(*, kernel_lister=None, rag_store=None, top_n=10, agent_config_dir=None,
    prompts_dir=None)` — defaults `kernel_lister` to the bare `list_top_kernels` function
    reference (no eager call/credential check)
  - `_build_messages` fetches kernels via `self._kernel_lister(state["competition_name"],
    n=self._top_n)`, stashes both the kernels and `competition_name` on `self` (documented
    rationale in the module docstring: `_write_output`'s signature carries no `state`, and
    `competition_analyst` is never re-entered concurrently — it isn't one of Phase 2's
    `parallel_nodes`), and appends a `## Kernels` HumanMessage block
  - `_write_output` parses the LLM's JSON array via `extract_json_array`, builds
    `IndexDocument`s via `build_index_documents` (`.text` = raw kernel metadata, never the LLM's
    summary; `relevance_score` enforced in `[0.0, 1.0]`; extraction indices must exactly cover
    `1..len(kernels)`), indexes them into a `RagStore` via `_ensure_rag_store` (injected or
    lazily constructed + cached, mirroring `web_researcher._ensure_rag_store` exactly), and
    writes a markdown report via `render_report_markdown` + `workspace.write_text`
  - Does **not** override `_build_output_state` (no `LabState` field for this output, matching
    `leakage_auditor`) — delta is `{"messages": [...]}` only
- `config/agents/competition_analyst.yaml` — `model_role: research`, `prompt_version: v1`,
  `tools: [kaggle_client]`, `output_file_pattern: "reports/competition_analysis_iter{iteration}.md"`,
  `max_tokens: 4096`
- `config/prompts/competition_analyst/v1.md` — explains the `## Kernels` input block, an explicit
  prompt-injection defense instruction (kernel titles/authors are untrusted Kaggle-user text, not
  instructions), the required JSON array output shape, and the evidentiary-limitation instruction
  (leave `methods_used`/`key_findings` empty rather than guess when a title reveals nothing)
- `tests/unit/nodes/llm/test_competition_analyst.py` (new, 13 tests) — real config/prompt load,
  no-arg construction with no network/credential call, ≥1 indexed document with non-empty
  `methods_used`, report content, delta touching only `messages`, `kernel_lister` call args,
  empty-kernel-list handling, out-of-range index → `ValueError`, duplicate index → `ValueError`,
  out-of-`[0,1]`-range `relevance_score` → `ValueError`, non-string list item → `ValueError`,
  malformed JSON → `ValueError`, lazy default-`RagStore` construction — the last four prove the
  node is wired to `_research_common`'s strict validation, not a weaker local reimplementation
- `docs/agents.md` — appended the `competition_analyst` row
- No `config/phases/*.yaml` change needed: `competition_analyst` was already registered in
  `config/phases/phase2_research.yaml`'s `nodes`/`sequence` (as the Phase 2 fan-in join node
  after `literature_researcher ‖ web_researcher`) by an earlier task.
- `tests/integration/phases/test_phase_subgraphs_smoke.py` and
  `tests/unit/nodes/llm/test_competition_analyst.py`'s sibling smoke coverage: extended the
  existing phase-subgraph smoke test's mocked-LLM dispatch with a `competition_analyst` JSON-array
  response, and patched `src.nodes.llm.competition_analyst.list_top_kernels`/`RagStore` so the
  smoke test (which constructs the node with no injected fakes via `resolve_node`) never hits the
  real Kaggle API or the Docker `chroma` service.
- `context/discoveries.md` — appended an OPEN entry documenting the forum-post API-surface
  investigation and its outcome.
- `context/decisions.md` — appended an entry documenting why forum-post scraping was dropped in
  favor of the small additive `list_top_kernels` function instead of a separate infra-agent task.

**Fix pass (post-review correction):** the initial implementation incorrectly believed
`_research_common.py` did not exist yet on this branch and shipped a local reimplementation of
its JSON-extraction/index-building/report helpers. It was wrong: T-017 merged
`_research_common.py` (commit c4cc8c2) *before* T-018 was even claimed (commit 9b776ac, 3 minutes
later) — the check that produced the original "doesn't exist" claim was run against a stale
worktree state, not HEAD. Review (code-quality + security + adversarial, independently
converging) found the local duplicate was strictly weaker than the shared helpers in three ways:
(1) **HIGH** — `_coerce_relevance_score` did a bare `float(value)` with no `[0.0, 1.0]` range
check, unlike `_research_common._validate_relevance_score`; a gamed/malformed kernel title could
push an out-of-range (or `inf`/`nan`, which Python's `json.loads` accepts by default) relevance
score into the RAG store, verified end-to-end against real ChromaDB to round-trip or silently
drop the document depending on the value; (2) extraction indices weren't required to exactly
cover `1..len(kernels)`, so a duplicate index could double-index one kernel while an omitted one
was silently dropped; (3) `_coerce_str_list` silently coerced non-string list items via `str()`
instead of raising. Fixed by refactoring `competition_analyst.py` to import and reuse
`extract_json_array`/`build_index_documents`/`render_report_markdown` from `_research_common.py`
(mirroring `web_researcher.py`'s usage exactly), deleting the local `_strip_outer_fence`,
`_extract_json_array`, `_build_index_documents`, `_coerce_str_list`, `_coerce_relevance_score`,
and `_render_report_markdown`. Net effect: `competition_analyst.py` shrank from 114 to 56
statements (100% coverage on both), and `relevance_score`/index-coverage/str-list validation now
match `_research_common.py`'s strictness exactly (proved by the four new tests listed above).

**Deviations from the plan / notes for the Orchestrator:**
- `LLMNode`'s real extension points are `_build_messages`, `_write_output`, `_build_output_state`
  (all confirmed by reading `src/nodes/llm/base.py` in full) — matched the plan's guesses exactly.
- `IndexDocument` fields (`src/memory/store.py`): `text, source, problem_type: list[str],
  methods_used: list[str], dataset_characteristics: list[str], key_findings: str,
  relevance_score: float, id (auto uuid4)` — used as-is, no guessing needed.
- One test-suite fix needed beyond the plan: the pre-existing `test_phase_subgraphs_smoke.py` and
  the checkpointer test both build real phase subgraphs via `resolve_node`, which now resolves
  `competition_analyst` to a real node instead of a `NoOpNode` — its default `kernel_lister`/
  `RagStore` would otherwise make a real Kaggle API call / try to reach the Docker `chroma`
  service during those tests. Fixed by extending the smoke test's existing autouse mock fixture
  (see above). The one remaining failure in the full suite
  (`tests/unit/graph/test_checkpointer.py::test_resume_after_restart_does_not_rerun_completed_phase`)
  reproduces identically on a clean pre-T-018 checkout (`git stash -u` verified) — pre-existing,
  unrelated to this task, not touched here.
- `ruff check . && ruff format --check .` and `mypy src/` all pass clean.
