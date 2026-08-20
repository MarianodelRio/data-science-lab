# Discoveries

Cross-agent alerts. When an agent finds something that affects another module, it writes here.

## Format

```
## OPEN — YYYY-MM-DD [Source agent → Target agent]
[What was found and what action is needed]
Status: open / resolved in T-XXX
```

## OPEN — 2026-08-04 [frontend-agent → pipeline-agent]
T-038 created `docs/api.md` early (human-approved scope adjustment) because it did not
yet exist and T-038's done-when requires a "Frontend client" note there. Content added:
a minimal endpoint reference table copied from `design.md` § UI architecture, plus a
"Frontend client" section describing `frontend/src/api/client.ts`. T-045 (docs skeletons,
owned by pipeline-agent) is expected to (re)create `docs/api.md` as a fuller "endpoint
reference skeleton (REST + SSE + WebSocket)". When T-045 runs, reconcile rather than
overwrite: merge the existing "Endpoints" table and preserve the "Frontend client"
section from T-038's version into the new skeleton.
Status: resolved in T-045 — merged during rebase: T-038's endpoint entries were split into
REST / SSE / WebSocket tables per T-045's skeleton headings, and the "Frontend client"
section was preserved unchanged.

## OPEN — 2026-08-04 [frontend-agent → api-agent]
`frontend/src/api/types.ts` (T-038) defines provisional TypeScript interfaces for
Run, PipelineEvent, ChatMessage, and related API response shapes, since `design.md`
only documents endpoint purposes, not JSON schemas. When the backend API tasks
(FastAPI routes under `src/api/`) land, please confirm or correct these shapes against
the real response payloads and update `frontend/src/api/types.ts` accordingly.
Status: open

## OPEN — 2026-08-04 [frontend-agent → infra-agent]
`frontend/` (T-038) requires Node.js `>=20.19.0` (pinned via `package.json` `engines`) —
current majors of Vite (8.x), ESLint (10.x), `@vitejs/plugin-react` (6.x), and Vitest
(4.x) all dropped support for Node 18. The dev machine's default `node`/`npm` on PATH
was Node 16.17.0 (and its `npm` shim was broken besides), so this task installed a
local Node 24 LTS tarball under the scratchpad to run `npm install`/`build`/`lint`/`test`;
nothing was changed system-wide. When `docker/` and `.github/` CI config are set up,
pin the frontend build/test image and any Node-based CI job to Node `>=20.19` (LTS 22/24
recommended) to match `frontend/package.json` engines.
Status: open

## OPEN — 2026-08-04 [infra-agent (T-002) → pipeline-agent (T-031 score_evaluator)]
Adversarial review of T-002 flagged that `LabState.best_score`/`last_score` have no documented
polarity convention. `new_state()` defaults `best_score=float("-inf")`, which correctly lets the
first experiment always register as an improvement, but only if downstream comparisons assume
"higher score is better." Kaggle competitions routinely use minimize-oriented metrics (RMSE,
LogLoss, MAE). If `score_evaluator` (Pipeline Phase 6) does a naive `last_score > best_score`
without first normalizing/sign-flipping minimize metrics, `best_score`/`best_experiment_path`
will silently freeze after the first experiment and never update again for those competitions —
a silent violation of invariant #3 (best-score-only-on-improvement) that no test would catch
since `float` accepts either polarity. Action needed: `score_evaluator` must normalize all scores
to "higher is better" before writing `last_score`, or `LabState` needs an explicit polarity
field — decide when T-031 is implemented. Documented in `docs/pipeline.md` § State in the
meantime.
Status: open

## OPEN — 2026-08-04 [infra-agent (T-002) → pipeline-agent (T-009 GraphBuilder)]
Adversarial review of T-002 flagged that only `LabState.messages` has a LangGraph reducer
(`add_messages`); every other field defaults to the `LastValue` channel, which raises
`InvalidUpdateError` if two nodes write the same key in the same super-step. `design.md`'s Phase
2 topology runs `literature_researcher` and `web_researcher` concurrently — the only sanctioned
parallel step in the pipeline (CLAUDE.md invariant #6 / design.md invariant #6, max 2 concurrent
LLM agents). When T-009 wires up the actual graph/supervisor, verify whether these two nodes (or
any future concurrent pair) ever write the same `LabState` key in the same step; if so, that
field needs an explicit reducer (e.g. an accumulator for `experiments`) before it can be safely
written concurrently — this would be a `LabState` contract change requiring the same
explicit-approval process as T-002. Documented in `docs/pipeline.md` § State in the meantime.
Status: open

## OPEN — 2026-08-04 [infra-agent (T-002) → pipeline-agent (any node writing `experiments`)]
Adversarial review of T-002 noted `LabState.experiments: list[dict]` has no nested type — the
entry shape (`{id, path, cv_score, iteration, model}`) lives only in a trailing comment in
`design.md`/`src/state.py`, not enforced by mypy. Low risk today (no code writes to this field
yet), but as ~25 future nodes come to read/write it, key-name drift (`cv_score` vs `score`, `id`
vs `exp_id`) becomes possible with no type-checker guardrail. Consider a nested
`ExperimentEntry(TypedDict)` when the first node that populates `experiments` is implemented
(likely T-020 baseline_runner or T-029 coder) — this would be a `LabState` type change and needs
the same explicit-approval process as T-002, so raise it as a proposed design.md amendment at
that time rather than drifting the type silently.
Status: open

## OPEN — 2026-08-04 [pipeline-agent (T-045) → infra-agent (T-001)]
T-045 created README.md with a "## What is this" + "## Documentation" (doc links) section, since it didn't exist yet when T-045 ran. T-001's Done-when checklist also requires "README updated with setup steps" — expect a whole-file conflict on README.md when both PRs merge (both branches independently create/modify it with non-overlapping intent: project description + doc links vs. setup/install steps). Whoever merges second should reconcile by keeping BOTH sections, not picking one side. Not an architecture issue — routine merge resolution, but flagging so it isn't silently resolved by dropping one section.
Status: resolved in T-045 — merged during rebase: README.md now keeps T-001's project
description, Prerequisites, Setup, Development commands, and Docker/CI sections, plus a
new "Documentation" section (added right after "Architecture") linking to all four docs.

## OPEN — 2026-08-05 [infra-agent (T-004) → future task touching src/llm/ or config/settings.yaml]
`ApiKeysConfig` (`src/config/settings.py`) only has fields for `anthropic`, `deepseek`, `groq`,
`kaggle_username`, `kaggle_key` — there is no `openai` or `gemini` field, because
`config/settings.yaml`'s `api_keys` section only lists keys for providers the current
`models.{role}` assignments actually use. `LLMFactory`'s `openai` and `gemini` provider wrappers
(`src/llm/factory.py`) are implemented and dispatchable (any role's `provider` can be set to
`openai`/`gemini` in `config/settings.yaml`), but they construct `ChatOpenAI`/
`ChatGoogleGenerativeAI` with no `api_key` kwarg at all — they rely entirely on the SDKs' own env
fallback (`OPENAI_API_KEY` / `GOOGLE_API_KEY` read directly from the process environment, bypassing
`Settings`/`${ENV_VAR}` resolution and its "missing var raises `ConfigError` naming the file"
guarantee). This is intentional for T-004 (matches the task's approved design), not a bug — but if
a future task actually routes a role to `openai` or `gemini` in `config/settings.yaml`, either add
`openai`/`gemini` fields to `ApiKeysConfig` and thread them through `_build_openai`/`_build_gemini`
the same way `anthropic`/`deepseek`/`groq` already work, or explicitly document the env-fallback
behavior in `.env.example` and `docs/configuration.md` so it isn't a silent gap when the process
env lacks the var (current behavior: the SDK raises its own error, not `ConfigError`).
Status: open

## OPEN — 2026-08-05 [infra-agent (T-007) → any future task calling kaggle_client.get_score/download]
Adversarial review of T-007 found two latent, low-probability failure modes in
`src/tools/kaggle_client.py`, both left unhandled as accepted low-severity gaps (not blocking
merge):
1. `get_score`'s `max(submissions, key=lambda s: s.date)` will raise a bare, decontextualized
   `TypeError` if any submission in the list has `date=None` (verified against installed
   `kagglesdk`'s `ApiSubmission`: `.date` has no `None`-safe fallback, unlike `.public_score`
   which is always coerced to `str`). Unconfirmed whether the real Kaggle API ever actually
   returns `date=None` for a real submission.
2. `download()` assumes the downloaded archive is always named `{competition}.zip`, but the real
   filename is server-URL-derived (per `kaggle`'s own `competition_download_files`
   implementation) and not guaranteed to be `.zip`. A mismatch produces a bare
   `FileNotFoundError` pointing at an invented path, with the actually-downloaded file silently
   orphaned in `dest_dir`.
If a future task hits either of these in practice (e.g. T-018 competition_analyst, T-033
reviewer+report_writer+kaggle_client node, or T-037 API kaggle+mlflow endpoints), wrap the
relevant call with a clearer error message naming the competition and the actual failure, rather
than letting the bare exception propagate.
Status: open

## OPEN — 2026-08-05 [infra-agent (T-008) → infra-agent (Docker/CI)]
`src/tools/rag.py`/`src/memory/store.py` (T-008) use
`sentence_transformers.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")` for
local embeddings. This model downloads from the Hugging Face Hub on first use if not already
present in the local cache (`~/.cache/huggingface` / `sentence-transformers` cache dir) — a
one-time network dependency, not a per-call one, so the "no external API call" done-when
criterion holds in steady state once cached. In a fresh Docker container (no persisted cache
volume) or a network-restricted CI runner, the very first `RagStore(...)` construction anywhere
in the process will attempt this download and fail/hang if there is no network access.
Not addressed as part of T-008 (out of its `src/tools/`/`src/memory/` folder scope; no
Docker/CI files touched). Whoever builds the `docker/` image or CI pipeline for the `chroma`
service / any node importing `src/tools/rag.py` should consider pre-caching the model into the
image (e.g. a `RUN python -c "from sentence_transformers import SentenceTransformer;
SentenceTransformer('all-MiniLM-L6-v2')"` layer) or mounting a persistent cache volume, so
CI/production runs never hit this cold-start network call.
Status: open

## OPEN — 2026-08-07 [pipeline-agent (T-014) → infra-agent (WorkspaceManager) / any future LLM node reading an upstream node's path field]
`WorkspaceManager.write_text`/`write_json` return an *absolute* path (`workspace_path` is
`.resolve()`d in `__init__`), and every `LLMNode._build_output_state` override stores that
return value verbatim into a `LabState` path field (e.g. `eda_report_path`,
`problem_definition_path`). But `WorkspaceManager.read_text`/`read_json`'s `_resolve` explicitly
rejects absolute `relative_path` input. `problem_framer` and `leakage_auditor` (T-014) are the
first nodes to ever read a path field written by an earlier node (previously only `data_analyst`
existed, which writes but doesn't read another node's output), so this is the first point the
inconsistency becomes reachable — it wasn't caught by T-002's or T-005's own test suites since
neither exercises the write-then-read-elsewhere round trip.
Worked around locally in both node files with a `_relative_to_workspace` helper that
re-relativizes an absolute stored path against the current `WorkspaceManager.workspace_path`
before reading (safe: the input to this helper is always a WorkspaceManager-produced path that
already passed `_resolve`'s sandboxing on write, never raw LLM output — confirmed by the T-014
security review). Not fixed at the source since `WorkspaceManager`'s public API is a protected
contract outside `pipeline-agent`'s `folders:`.
If a future task adds another node that reads a path field from `LabState` (increasingly likely
as more Phase 1+ nodes chain off each other), consider fixing this at the source instead of
duplicating the workaround again: either make `write_text`/`write_json` return a workspace-relative
path, or make `read_text`/`read_json` accept an absolute path that resolves inside
`workspace_path`. Requires human approval as a protected-contract change.
Status: open

## OPEN — 2026-08-07 [pipeline-agent (T-015) → pipeline-agent (T-016 analysis_critic)]
`config/phases/phase1_understanding.yaml`'s `critic.targets` already lists `validation_strategist`
as a valid critic-retry target (`max_retries: 3`), even though `analysis_critic` (T-016) hasn't
landed yet. `validation_strategist` (T-015, `src/nodes/llm/validation_strategist.py`) enforces
CLAUDE.md invariant #1 (`validation/fold_config.json` is write-once/frozen after Phase 1) via an
unconditional `FoldsAlreadyFrozenError` raised the moment the file already exists — nothing in
`src/graph/` catches exceptions around node execution today (confirmed: no relevant `try`/`except`
around node invocation in `src/graph/*.py`), so once `analysis_critic` routes an `iterate` verdict
back to `validation_strategist`, the node's *second* invocation hits the write-once guard
immediately and raises uncaught, crashing the whole graph run on the very first retry attempt —
before `max_critic_retries` is ever consulted. This silently defeats invariant #5 ("critics enforce
max_critic_retries then force pass — no infinite loops") specifically for this node.
When T-016 lands, either (a) exclude `validation_strategist` from `phase1_understanding.yaml`'s
`critic.targets` (folds are meant to be chosen right the first time, not iterated on — arguably the
correct fix, but is a `config/phases/*.yaml` change requiring the same protected-contract approval
as any other), or (b) have the critic-retry wiring special-case `FoldsAlreadyFrozenError` as an
implicit "pass" rather than letting it propagate. Found during T-015's adversarial review; no code
changed for this task since the critic doesn't exist yet.
Status: open

## OPEN — 2026-08-07 [pipeline-agent (T-017) → whoever next touches RagStore/IndexDocument id generation or Phase-2 checkpointing]
Adversarial review of T-017 (`literature_researcher`/`web_researcher`) flagged that the RAG store
has no content-based deduplication. `IndexDocument.id` (`src/memory/store.py`, T-008) defaults to a
random `uuid4()` with no dependency on `text`/`source`/`url`, and `RagStore.index()` only rejects
duplicate `.id` values *within a single call* — it has no notion of "this content was already
indexed in a previous call." Two concrete ways this produces duplicate entries in the same
competition's Chroma collection:
1. `LiteratureSearchClient.search()` merges arxiv + Semantic Scholar results with no cross-source
   overlap check — the same paper indexed by both APIs (common: many arxiv preprints are also in
   Semantic Scholar) becomes two separate `IndexDocument`s with different random ids but
   near-identical `text`.
2. Phase 2 has no per-node checkpointing today (unlike Phase 1's `interrupt_after`) — if a run
   crashes/restarts after `literature_researcher`/`web_researcher` already called `.index()` but
   before the graph's checkpoint advances past Phase 2, a resume re-runs both nodes and indexes
   the same sources again under fresh random ids.
Neither is fixable within T-017's own `folders:` (`src/nodes/llm/`, `config/agents/`,
`config/prompts/`): (1) would need a deterministic id scheme (e.g. hash of `source`/`url`) in
`IndexDocument`/`RagStore`, which is T-008's frozen/protected schema; (2) is a `src/graph/`
checkpointing concern, outside pipeline-agent node-task scope and requiring its own design work.
Not addressed as part of T-017 — low severity today (duplicate RAG entries degrade retrieval
quality/cost, they don't corrupt state or violate an invariant), but worth fixing before Phase 2
sees real repeated runs. Suggested direction for whoever picks this up: derive `IndexDocument.id`
deterministically from `source` (or `source` + a content hash) so `RagStore.index()`'s upsert
semantics naturally de-duplicate re-indexed content, rather than adding a separate dedup pass.
Status: open

## OPEN — 2026-08-07 [pipeline-agent (T-018) → pipeline-agent (T-017 literature_researcher/web_researcher), infra-agent (future kaggle_client callers)]
Investigated whether `competition_analyst` (T-018) could pull Kaggle competition forum/discussion
posts, per the original task file's "forum posts" wording. Confirmed against the installed
kaggle/kagglesdk packages: there is no discussions/forum RPC client wired into KaggleClient, and
KaggleApi itself declares no discussion*/forum*/topic*/comment*-named public method — only typed
message definitions exist for discussions, no backing service. So there is no way to list/fetch
forum posts through the installed SDK short of scraping kaggle.com HTML, which is out of scope for
kaggle_client.py's "thin wrapper around the kaggle package" contract.
Per human-approved scope adjustment, T-018 dropped "forum posts" entirely (no stub, no partial
implementation) and instead added kaggle_client.list_top_kernels(competition, n=10, api=None) — a
small, additive wrapper over KaggleApi.kernels_list(competition=..., sort_by="voteCount") — as the
only new kaggle_client.py surface; download/submit/get_score were left untouched.
A direct consequence: competition_analyst's LLM extraction is grounded only in kernel
titles/authors/vote counts, never notebook code/output — flagged in
config/prompts/competition_analyst/v1.md itself as a real evidentiary limitation.
If a future task needs actual Kaggle forum content, re-verify against whatever kaggle/kagglesdk
version is installed at that time before assuming this still holds. Adding it, like
list_top_kernels here, should stay a small additive kaggle_client.py function rather than
triggering a dedicated infra-agent task.
Status: open

## RESOLVED — 2026-08-10 [Orchestrator (/orchestrate T-019) → pipeline-agent/infra-agent (whoever owns src/graph/ checkpointing)]
While verifying T-019 (`memory_manager`), `tests/unit/graph/test_checkpointer.py::test_resume_after_restart_does_not_rerun_completed_phase` failed on a clean checkout of `origin/main` (verified via a separate clone, independent of any T-019 change): `assert call_counts.get("data_analyst", 0) == 1` fails with `data_analyst` actually called 4 times. This means resuming a run from a checkpoint after a restart currently re-runs `data_analyst` (and likely the rest of phase1) multiple times instead of skipping the already-completed phase — a real bug in the checkpointer/resume path, not a flaky test.
Not fixed here: `src/graph/` is outside T-019's `folders:` (`src/nodes/llm/`, `config/agents/`, `config/prompts/`), and the fix likely requires understanding `src/graph/checkpointer.py`'s interrupt/resume wiring, not a one-line change.
**Correction (B-001, 2026-08-13) — the claim above that this is "a real bug in the checkpointer/resume path" is wrong.** The original observation (`data_analyst` called 4 times) was real; the diagnosis was not. `src/graph/` was never touched by the fix. The two real defects were both test-side: (1) the test's mocked LLM had no `analysis_critic` branch, so the fallback response was normalized to `iterate` by `_parse_verdict` and the critic legitimately re-invoked `data_analyst` `max_retries: 3` times before forcing a pass (CLAUDE.md invariant #5) — 1 graph call + 3 critic retries = the observed 4; (2) whether those retries were counted at all depended on module import order, because `analysis_critic` binds `resolve_node` at import time (`src/nodes/llm/analysis_critic.py:31`) while `src/graph/builder.py:70-73` deliberately goes through the module attribute — so run alone the test failed `assert 4 == 1`, and run after anything importing `analysis_critic` it instead made a live Kaggle API call through `competition_analyst`'s default `list_top_kernels`.
Observed evidence after the fix: across a simulated restart every phase-1 node count stays at 1, the first `invoke` halts at `next == ('phase2_research',)`, and `invoke(None)` runs straight through phases 2-4 to halt at `next == ('phase5_implementation',)` — i.e. no completed phase re-executes.
Status: resolved in B-001

## OPEN — 2026-08-11 [pipeline-agent (T-023) → whoever next tunes specialist_selector / builds T-025 (deep_learning_specialist), T-026 (nlp_specialist), T-027 (timeseries_specialist)]
`specialist_selector` (`src/nodes/compute/specialist_selector.py`) selects a specialist via a deterministic keyword-precedence match against a normalized text blob (`problem_type` + `model_families` + `order` + `rationale`) — approved v1 scope is plain keyword matching, not NLP-level negation/sentiment parsing. This means it has no awareness of negation or the semantic role a keyword plays in the sentence, so a keyword's mere presence can misroute a plan even when the surrounding text clearly means the opposite or means something unrelated to model choice. Two concrete repro examples, confirmed by adversarial review: (1) `rationale: "No BERT/transformer approach needed here, sticking with gradient boosting."` still matches the NLP keywords "bert"/"transformer" and selects `nlp_specialist`, despite the rationale explicitly rejecting that approach. (2) `model_families: ["Prophet-inspired feature engineering for XGBoost"]` still matches the timeseries keyword "prophet" and selects `timeseries_specialist`, even though "Prophet" there qualifies a feature-engineering idea for an XGBoost model, not the model family itself.
Accepted as a design tradeoff for v1 (reviewers explicitly did not block T-023 on this) — a misrouted specialist just means a plausible-but-suboptimal specialist gets tried for one iteration, not a leakage/correctness invariant violation, and it's a low-frequency phrasing pattern for an LLM-authored `solution_plan.json`. Not fixed here: negation-aware matching is a different, larger mechanism (dependency parsing or an LLM-based classification step) than the curated-keyword/word-boundary convention this task's approved scope specified. Worth revisiting once T-025/T-026/T-027 land for real and there's an actual population of misrouted plans to measure against, rather than guessing at a negation heuristic now with no real specialist behavior yet to validate it against.
## OPEN — 2026-08-11 [pipeline-agent (T-024) → pipeline-agent (T-031 score_evaluator / T-032 iteration loop)]
**Nothing in `src/` ever increments `state["current_iteration"]`.** Verified by grep across `src/`
while implementing T-024: `src/state.py:74` sets it to `0` in `new_state`, `src/graph/supervisor.py`
and `src/observability/jsonl_callback.py` only *read* it, and every remaining hit is a node reading
it to interpolate an output path. There is no writer anywhere.
Consequence: every design→implementation cycle resolves to the same
`experiments/exp_0/design.json` and silently overwrites the previous iteration's design — the
pipeline looks like it is iterating while producing exactly one artifact per path forever.
This is **pre-existing and not specific to T-024**: the identical overwrite already affects T-021's
`design/iteration_{iteration}/solution_plan.json`, T-022's
`design/iteration_{iteration}/feature_spec.json`, and `competition_analyst`'s
`reports/competition_analysis_iter{iteration}.md`. `analysis_critic` already documents the same root
cause and works around it for its own output with an extra `{phase}` placeholder
(`src/nodes/llm/analysis_critic.py:207-230`).
Not fixed here: an id allocator or a `current_iteration` writer is a protected-contract
(`src/state.py`) or `src/graph/` change, both outside T-024's `folders:`
(`src/nodes/llm/`, `config/agents/`, `config/prompts/`).
Whoever lands the iteration loop (T-032, or T-031 `score_evaluator` if the increment lands with the
scoring step) must increment `current_iteration` exactly once per completed cycle — and should
sanity-check every `{iteration}`-bearing `output_file_pattern` at that point, since they all start
producing distinct files only from that commit onward.
Status: open

## RESOLVED — 2026-08-11 [pipeline-agent (T-024) → whoever merges second of PR #25 (T-023) and T-024]
**Expected `docs/pipeline.md` merge conflict, plus a docs/YAML ordering dependency.**
Both branches add a `### Implementation (Phase 5)` section immediately after `### Baseline
(Phase 3)`, and both add a row to the "Node classification" table. Resolve by keeping **both**
under a **single** `### Implementation (Phase 5)` heading — T-023's `specialist_selector` bullet
first, then T-024's `classical_ml_specialist` bullet and the `#### The design.json contract` block
— and keep both table rows. Same reconcile-don't-pick-a-side situation as the T-045/T-001 README
entry above. `context/decisions.md` and `context/discoveries.md` will conflict at EOF for the same
reason (both append-only); keep both blocks.
`config/phases/phase5_implementation.yaml` is **not** touched by T-024, so there is no conflict
there — but note the dependency: T-024's new `docs/agents.md` step-3 exception note states that the
5 Phase-5 specialists are not listed in that YAML, which only becomes true once PR #25 lands its
(human-approved) trim of `nodes`/`sequence` to `[specialist_selector, coder, code_critic]`. If
T-024 merges first, that note is forward-looking for exactly as long as PR #25 stays open; until
then `classical_ml_specialist` is wired as a real graph edge in phase 5 and runs unconditionally.
The phase-5 subgraph smoke test passes either way (T-024 added the mocked-LLM dispatch entry for
`classical_ml_specialist`), so nothing fails loudly to flag it — hence this note.
Resolution (2026-08-12): PR #25 merged first, and T-024 was rebased onto it. The three predicted
conflicts (`docs/pipeline.md`, `context/decisions.md`, `context/discoveries.md`) occurred exactly
as described and were resolved keep-both as prescribed: one `### Implementation (Phase 5)` heading
with T-023's `specialist_selector` bullet, then T-024's `classical_ml_specialist` bullet and the
`#### The design.json contract` block; both "Node classification" rows kept. Because #25 landed
first, the forward-looking caveats in `docs/agents.md` step 3 and `docs/pipeline.md` were dropped —
`config/phases/phase5_implementation.yaml` no longer lists the specialists, so
`classical_ml_specialist` is dispatched by `specialist_selector`, never a standalone graph edge.
Status: resolved

## OPEN — 2026-08-12 [pipeline-agent (T-024) → pipeline-agent (whoever owns a `src/nodes/llm/base.py` refactor)]
`_strip_outer_fence`/JSON-extraction is now duplicated **seven** times across `src/nodes/llm/`:
`problem_framer.py`, `leakage_auditor.py`, `analysis_critic.py`, `baseline_designer.py`,
`feature_engineer.py`, `solution_architect.py`, `_research_common.py` (array variant), plus
`_experiment_design.py` (object variant, T-024). T-020 already hoisted `relative_to_workspace` into
`src/nodes/llm/base.py` on exactly this reasoning at three copies.
Proposal: one small dedicated task that moves a `strip_outer_fence(content, node_name)` /
`extract_json_object(content, node_name)` / `extract_json_array(content, node_name)` trio into
`base.py` and migrates all seven call sites, deleting the private copies.
Not done in T-024: `base.py` is imported by every LLM node, so touching it from a node task means
either migrating six landed modules in a PR scoped to one node, or adding an eighth copy's worth of
surface to `base.py` and leaving the duplication anyway. Note for whoever picks it up:
`_experiment_design.extract_json_object` is deliberately more permissive than its siblings (it
salvages a brace-delimited slice when the whole-text parse fails — see the 2026-08-12 T-024
decision-log entry), so a naive merge would either regress that tolerance or silently extend it to
five nodes that were reviewed on the stricter behavior. Preserve the difference explicitly.
2026-08-18 (T-032): the copy count went **8 → 9, not 8 → 11**. The three Phase 6 LLM nodes
(`error_analyst`, `hypothesis_generator`, `experiment_designer`) share ONE copy inside the new
private `src/nodes/llm/_evaluation_llm_common.py` rather than carrying one each. That copy is the
permissive (brace-salvage) variant, for the same reason `_experiment_design`'s is —
`config/phases/phase6_evaluation.yaml` declares `critic: null`, so those three nodes have no retry
wrapper at all. `base.py` was deliberately not touched.
Status: open

## RESOLVED — 2026-08-12 [pipeline-agent (T-024) → pipeline-agent (T-025, T-026, T-027, T-028)]
**All five Phase-5 specialists write the same path.** `output_file_pattern` is
`experiments/exp_{iteration}/design.json` for `classical_ml_specialist`, and T-025–T-028 are
specified the same way. The only thing distinguishing the outputs is the `specialist` field
*inside* the file. Today that is harmless — `specialist_selector` (T-023) activates exactly one
specialist per iteration — but it means the path scheme carries no specialist identity, so any
future change that runs two specialists in the same iteration (an ensembling pass reading two
candidate designs, a comparison run, a retry after a different route) silently overwrites instead
of accumulating.
Same root cause as the `current_iteration` entry above: there is exactly one design slot per
iteration. Whoever lands T-025 should decide the path scheme for all four remaining specialists at
once — either `experiments/exp_{iteration}/design.json` stays and the constraint "one specialist
per iteration" gets written down as an invariant, or the pattern grows a specialist component
(`experiments/exp_{iteration}/{specialist}/design.json`) and `coder` (T-029) is told how to find
it. Not decided in T-024: it is a design decision affecting four unstarted tasks and their
consumer.
Status: resolved in T-025 — the pattern stays `experiments/exp_{iteration}/design.json` for all five
specialists, and the "one specialist per iteration" constraint is now written down in
`docs/pipeline.md` § The design.json contract, with the escape hatch recorded in the 2026-08-12
T-025 entry in `context/decisions.md`. Human-confirmed at T-025's Phase-1 checkpoint.
Independently confirmed by T-026 (2026-08-13), which reached the same conclusion at its own
Phase-1 checkpoint before T-025 had merged: keep the shared path and record "exactly one specialist
runs per iteration" as the invariant it relies on (true by construction at
`specialist_selector.py:227-233`); a specialist-namespaced path was considered and discarded as
premature, since no current mechanism runs two specialists in one iteration and the change would
alter T-027/T-028's consumer contract. Two independent agents converging on the same ruling is the
strongest signal available that this is the right default — but note it was decided twice in
parallel, which is exactly the duplicated-work failure mode the discovery was written to prevent.

## OPEN — 2026-08-12 [pipeline-agent (T-024) → pipeline-agent (T-029 coder / T-031 score_evaluator)]
**`FORBIDDEN_CV_KEYS` is a tripwire, not a proof.** T-024's validator rejects a design that names
`cv`/`cv_strategy`/`folds`/`fold_indices`/`n_folds`/`n_splits`/`validation`/`test_size`/`shuffle`,
which catches an explicit attempt to redefine cross-validation. It does **not** catch a model that
carves its own holdout out of the training fold through ordinary model-side hyperparameters:
`validation_fraction` (sklearn's `HistGradientBoostingClassifier`/`MLPClassifier`),
`early_stopping`, `n_iter_no_change`, `eval_set`/`early_stopping_rounds` (xgboost/lightgbm),
`od_type`/`od_wait` (catboost). Any of these can shrink the effective training data or leak the
fold's validation split into the stopping decision, making the recorded CV score
non-comparable against the baseline and against other experiments.
Deliberately not added to `FORBIDDEN_CV_KEYS` in T-024: early stopping evaluated against the frozen
fold's *own* validation split is legitimate, widely-used practice, and banning it outright is a
modeling decision that would be made unilaterally on behalf of four unstarted specialist tasks
(T-025–T-028). The `FORBIDDEN_CV_KEYS` docstring now states this limitation explicitly rather than
implying the guard is exhaustive.
For T-029/T-031: when generating the training script, decide per model family whether these
parameters are honored, translated to use the frozen fold's validation split, or dropped — and
make the choice visible in the experiment results so a score computed under early stopping is not
silently compared against one that was not.
Status: open

## OPEN — 2026-08-12 [pipeline-agent (T-024) → pipeline-agent (whoever does the `src/nodes/llm/base.py` reader/extractor hoist)]
**The `_read_*` upstream-artifact helpers across `src/nodes/llm/` all under-catch.** Every copy —
`feature_engineer._read_solution_plan`/`_read_eda_report`, `baseline_designer._read_problem_definition`/
`_read_eda_report`, `solution_architect`'s and `analysis_critic`'s readers, and
`_research_common.read_problem_type` — catches `OSError` alone while documenting (or plainly
implying) that it degrades rather than raises. Three inputs escape all of them:
1. a truncated/empty JSON artifact — `json.JSONDecodeError` (a `ValueError`),
2. an artifact that is not valid UTF-8 — `UnicodeDecodeError` (also a `ValueError`),
3. an absolute path recorded before the workspace was moved, renamed or bind-mounted elsewhere, or
   one containing `..` — `ValueError` out of `Path.relative_to`/`WorkspaceManager._resolve`.
Additionally, a pathologically nested payload (~993 levels) raises `RecursionError`, which is a
`RuntimeError` and so is caught by none of the above — and it recurses again inside `json.dumps`
when the reader pretty-prints the artifact back out, so the guard has to cover the serialization
too, not just the read.
Each one aborts the whole graph run from a code path whose entire purpose is to survive a missing
or malformed upstream artifact. Reproduced through the real phase-5 subgraph for T-024's own
readers.
Fixed **only** inside T-024's own modules (`src/nodes/llm/_experiment_design.py`,
`src/nodes/llm/classical_ml_specialist.py`), which now share a
`DEGRADE_ERRORS = (OSError, ValueError, RecursionError)` tuple plus an `isinstance(path, str)`
guard. The sibling modules are outside this task's scope and were deliberately left alone rather
than touched from a node task.
Whoever picks up the `base.py` hoist proposed in the entry above should fix these at the same time
— the same PR is already migrating all seven call sites, and `DEGRADE_ERRORS` is the natural thing
to hoist alongside the extractor trio.
2026-08-18 (T-032): `src/nodes/llm/_evaluation_llm_common.py` ships with the full
`DEGRADE_ERRORS = (OSError, ValueError, RecursionError)` tuple, the `isinstance(path, str)` guard
and the `json.dumps` guard from day one, so the three Phase 6 LLM nodes add no new under-catching
readers. The sibling modules named above are still unfixed.
Status: open

## OPEN — 2026-08-12 [pipeline-agent (T-025) → pipeline-agent (T-029 coder, T-031 score_evaluator), cross-ref T-047]
**Fit scope is unrepresentable in `design.json`, and the leak it allows is invisible to every
existing guard.** `preprocessing` is a flat list of lower_snake tokens with no notion of *when* a
step is fitted, and `FORBIDDEN_CV_KEYS` matches dict **keys**, never list **values** — so a design
naming `standard_scaling` or `median_imputation` says nothing about whether the scaler/imputer is
fitted over the full training set or inside each fold. Fitted over the full set, it leaks feature
statistics from every validation fold into training: the CV score against the frozen folds is
inflated and is no longer comparable to any other experiment in the run, which is a silent violation
of the comparability guarantee `validation/fold_config.json` exists to provide. Nothing in the
validator can catch it, because the design is textually identical either way.
This matters much more for neural designs than for trees: `classical_ml_specialist`'s families are
scale-invariant and consume categoricals natively (its example tokens are literally
`no_scaling_required`/`native_categorical_handling`), whereas TabNet/NODE/MLP all *require* fitted
scaling and imputation.
T-025 addresses it in prompt wording only (§ Preprocessing scope requires fitting inside each fold
and recommends encoding the scope in the token itself, e.g. `standard_scaler_fitted_per_fold`) —
deliberately not in the validator, since extending the schema means editing the shared contract that
T-026–T-028 inherit. Action needed: `coder` (T-029) must fit every fitted preprocessing step inside
the fold when it generates the training script, regardless of what the token says, and must not trust
the token to be well-named.
Cross-reference: T-047 introduces `fit_scope: global | per_fold` on `feature_spec.json` entries and
classifies scalers/normalizers as mandatorily `per_fold`. These are two schemas describing the same
property — whoever lands T-047 or T-029 should converge them on one fit-scope vocabulary rather than
letting `feature_spec.json` and `design.json` grow separate ones.
Two concrete holes verified by T-025's security review, both worth pinning down at T-029 time
(neither is a T-025 defect — both are properties of the shared T-024 contract, but neural designs are
what make them reachable, since they are the designs that actually want a validation split):
(1) `FORBIDDEN_CV_KEYS` is **case-sensitive** — `fixed_params: {"Shuffle": true}` is accepted and
reaches `design.json` while `"shuffle"` is rejected;
(2) the non-CV-key holdout escape hatch has concrete names: `validation_split` (the Keras kwarg) and
`val_size` both pass every guard, because only `validation` and `test_size` are banned. T-025
mitigates in prompt prose ("Never carve your own holdout") but nothing enforces it.
Status: open

## OPEN — 2026-08-12 [pipeline-agent (T-025) → pipeline-agent (T-029 coder), infra-agent (`pyproject.toml`)]
**PyTorch is not a dependency, so `coder` will generate a neural script that cannot run.**
`pyproject.toml` lists `optuna`, `xgboost`, `lightgbm` and `catboost`, but no `torch` and no
`pytorch-tabnet`; `design.md` marks PyTorch as "Optional ML (deep_learning_specialist only)".
Harmless for T-025 itself — the node only *designs* an experiment, imports nothing neural, and its
tests mock the LLM — but the moment `specialist_selector` routes a real run to
`deep_learning_specialist` and `coder` (T-029) turns the resulting `design.json` into a training
script, `code_executor` will run it and it will `ImportError` on the first line.
Not added here: `pyproject.toml` is infra-agent's, and which of `torch`/`pytorch-tabnet` is needed
depends on how `coder` implements the three families (an `mlp` needs only `torch`; `tabnet` and
`node` pull in more). Whoever lands T-029 should decide the dependency set and coordinate the
`pyproject.toml` change — and note that adding `torch` materially changes image size and CI time, so
the docker/CI config (a protected contract) is affected too.
Status: open

## OPEN — 2026-08-12 [pipeline-agent (T-025) → pipeline-agent (T-026 nlp_specialist)]
**T-025 and T-026 are in flight simultaneously and will conflict in four files.**
`origin/feature/T-026-node-nlp-specialist` was claimed while T-025 was being implemented (T-025's
own Phase-1 analysis saw an empty `tasks/in-progress/`). Both are Phase-5 specialists built from the
same T-024 template, so both PRs touch: `tests/unit/nodes/compute/test_specialist_selector.py`,
`docs/agents.md` (the agent table), `docs/pipeline.md` (the Phase-5 section and the node
classification table), and both context files.
The sharp edge is the selector test. T-025 re-pointed the two "unlanded specialist" tests from
`deep_learning_specialist` to **`nlp_specialist`**, because that was the next unlanded specialist —
which is precisely the node T-026 is landing. Whichever PR merges second must re-point them again,
to `timeseries_specialist` (T-027), and add a landed-case test for its own node. Merging T-026
without doing so reintroduces exactly the defect this hand-off exists to prevent: a unit test that
dispatches into a real `LLMNode` and attempts a live API call on any machine with API keys set.
The remaining conflicts are ordinary append/table merges — keep both rows, keep both entries.
Note for future task selection: this pattern repeats for T-027 and T-028. Landing the five
specialists in parallel guarantees this conflict every time; sequencing them, or moving the
NoOp-fallback test to a specialist that will never land, would remove it permanently.
Update (T-025's adversarial review, verified): T-026 is PR #27 and is **already open**, so T-025
merges second and re-pointed the two tests to `timeseries_specialist` (T-027) within its own PR
rather than leaving it to whoever merges after. Two things the original note got wrong or missed:
(1) the sharp part merges **without a conflict marker** — a real `git merge` of the two branches
conflicts only on the prose comment and the two *added* landed-case tests, while the two edited test
*bodies* merge silently as one side's version. Resolving the visible hunks and keeping both tests
ships a stale route; with fake API keys set, the merged tree produced a real `401` from a live
provider call inside a unit test. Whoever lands T-027 must therefore diff the test bodies explicitly,
not just resolve markers.
(2) T-026 **does** modify `src/nodes/llm/_experiment_design.py` — it hoists `read_solution_plan` into
the shared module. No functional conflict with T-025 (which only imports from that module), but once
T-026 lands there will be four copies of that reader on main, and `deep_learning_specialist`'s own
copy becomes the redundant one: it should switch to the shared `read_solution_plan`, and
`classical_ml_specialist`/`feature_engineer` should follow. That belongs to the `base.py`/reader hoist
task already logged above, not to a node task.
Status: open

## OPEN — 2026-08-12 [pipeline-agent (T-025) → pipeline-agent (T-029 coder), whoever adds a critic-retry wrapper]
**`_experiment_design.py`'s "every failure is a `ValueError`" contract does not hold on the
LLM-response path.** `_parse_json` catches only `ValueError`, but `json.loads` raises
`RecursionError` (a `RuntimeError`) on a sufficiently nested payload — measured threshold **994
levels, ~6 KB**, well within any model's output budget. Driven through the real
`deep_learning_specialist` with a 5000-deep response: the exception escapes as `RecursionError`, does
**not** name the specialist, and nothing is written.
This is an asymmetry rather than an oversight: the module already guards `RecursionError` on the
*upstream-read* path through `DEGRADE_ERRORS` (tested at depth 100 000), with a comment explaining the
~993-level threshold. The response path never got the same treatment. Identical for
`classical_ml_specialist`, so it is a property of the T-024 contract, not a T-025 defect — but it
matters specifically because these nodes have no retry wrapper today, and a future wrapper written to
catch `ValueError` (exactly what the module docstring promises is sufficient) will not catch this.
Fix when `_experiment_design.py` is next legitimately opened: add `RecursionError` to `_parse_json`'s
catch, or wrap `extract_json_object`'s body. Not done here — that module is frozen for T-025, and
T-026 is concurrently modifying it.
Status: open

## OPEN — 2026-08-12 [pipeline-agent (T-025) → pipeline-agent (T-026, T-027, T-028, T-029)]
**The selector's routing vocabulary and the specialists' `model_family` vocabularies are disjoint, and
a specialist that echoes the plan's own wording aborts the phase.** `specialist_selector` routes to
`deep_learning_specialist` on `_DEEP_LEARNING_KEYWORDS` = `neural`, `cnn`, `rnn`, `deep learning`,
`pytorch`, `lstm`. But T-025's `_MODEL_FAMILIES` only accepts `tabnet`/`node`/`mlp` and their aliases,
so **none** of `neural network`, `neural net`, `dnn`, `deep neural network`, `fully connected network`
resolves — each is a hard `ValueError`. The same asymmetry exists for `classical_ml_specialist`
(routed by `gradient boosting`, which is not an accepted family either), so this is template-inherited,
not new.
Why it matters more than it looks: these nodes have **no retry wrapper** — Phase 5's `code_critic`
targets `coder`, not the specialists (`extract_json_object`'s docstring says so explicitly). So an LLM
that answers with the plan's own phrasing (`"model_family": "neural network"`) aborts the phase with no
artifact at all, which is precisely the outcome each specialist prompt's "never refuse / design
something defensible, always" section exists to prevent. Prompt wording is the only thing standing
between the two vocabularies.
Not changed in T-025: adding generic aliases means deciding what a generic answer *maps to* (mapping
`"neural network"` → `mlp` is coherent with this prompt's own "prefer the lowest-capacity family"
guidance, but it is a modeling decision), and the fix should be uniform across all five specialists
rather than invented per node. Options for whoever takes it: (a) add generic aliases per specialist,
(b) give the specialists a retry wrapper, or (c) have `coder`/a critic treat an unresolvable
`model_family` as recoverable. Worth deciding once, for all five.
## OPEN — 2026-08-12 [pipeline-agent (T-026) → pipeline-agent (T-025/T-027/T-028 + whoever owns a `normalize_model_family` change)]
**`normalize_model_family` has no longest-match-wins rule**, so a modifier that qualifies another
family's alias is silently dropped rather than resolving correctly or conflicting. Found by
adversarial review of T-026: `normalize_model_family` raises "ambiguous" only when two *complete*
alias phrases from different families are both literally present as substrings. A phrase like
"fine-tuned sentence transformer" contains the complete `sentence_embeddings` alias "sentence
transformer" but no complete phrase from any other family's alias table (unless one happens to be
added) — it matches `sentence_embeddings` alone and the "fine-tuned" modifier, which actually
changes the intended family, is discarded with no signal. `coder` (T-029) dispatches on the
resolved `model_family` and nothing cross-checks it against `rationale`, so this silently writes a
`design.json` whose `model_family` contradicts its own `rationale` and yields the wrong training
script.
This class of bug is specific to any specialist whose families sit on a frozen-vs-fine-tuned (or
similarly modified) axis — it does not exist in `classical_ml_specialist`'s four families, which are
distinct model brands with no such modifier axis.
**T-026's mitigation (local, not a fix):** `nlp_specialist`'s own `_MODEL_FAMILIES["transformer_finetune"]`
now includes bare fine-tune-modifier tokens ("fine tune", "fine tuned", "fine tuning",
"finetune", "finetuned", "finetuning"). Any of these co-occurring with a `sentence_embeddings` (or
`tfidf_linear`) alias now makes both families match, which routes into the *already-existing*
"ambiguous" rejection instead of a silent misclassification — a hard raise is strictly better than
a wrong, silent resolution. This is a workaround confined to `nlp_specialist.py`'s own alias table,
not a change to `_experiment_design.py`.
**Residual cost:** a semantically clear phrase like "fine-tuned sentence transformer" — which a
human would read as unambiguously meaning `transformer_finetune` with a specific
(embeddings-derived) architecture — now raises instead of resolving. The mitigation cannot
distinguish "modifier legitimately changes the family" from "modifier is incidental prose near an
unrelated family name"; it treats both as ambiguous.
**Not fixed here:** the general fix is precedence by longest match (or some other overlap-resolution
rule) in `_experiment_design.normalize_model_family` — a shared-contract change affecting
`classical_ml_specialist` and every unstarted sibling specialist (T-025, T-027, T-028), out of
bounds for a single node task. Whoever next builds a specialist whose families sit on a similar
modifier axis (or whoever revisits `normalize_model_family` itself) should decide the general rule
once, rather than each specialist re-solving it locally with its own alias-table workaround the way
T-026 did.
Status: open

## RESOLVED — 2026-08-13 [Orchestrator (/bug B-001) → pipeline-agent]
**Correction to the 2026-08-10 T-019 entry above: the resume path is not broken.** That entry
concluded from `test_resume_after_restart_does_not_rerun_completed_phase` that resuming a run
re-executes phase 1 four times. Verified false by driving `GraphBuilder().build()` directly with
`analysis_critic` mocked to a `pass` verdict: every phase-1 node runs exactly once, the first
invoke stops at `next = ('phase2_research',)`, and after a simulated restart the phase-1 counts
stay at 1 while phase 2 executes once and the run continues into phase 3. `src/graph/`'s
checkpointer, `interrupt_after` wiring and SQLite thread handling are all correct.
The real defects are in the test, and there are two: (1) its mocked LLM has no `analysis_critic`
branch, so the critic really does re-invoke `data_analyst` `max_retries: 3` times before forcing a
pass (correct behavior, invariant #5) — 1 + 3 = the observed 4; (2) whether those retries are even
counted depends on import order, because `analysis_critic` binds `resolve_node` at import time
(`src/nodes/llm/analysis_critic.py:31`) instead of going through the module attribute the way
`src/graph/builder.py:70-72` deliberately does. Run alone the test fails `assert 4 == 1`; run after
anything that imports `analysis_critic` (i.e. the full suite) the assert passes and the test
instead makes a **live Kaggle API call** through `competition_analyst`'s default
`list_top_kernels`, violating the "no network calls in unit tests" gate.
Filed as B-001 (test-only scope; `src/` needs no change). Full diagnosis, including the phase-3/4
mocks the resume actually needs, is in `tasks/available/B-001-resume-reruns-completed-phase.md`.
Status: resolved in B-001

## RESOLVED — 2026-08-13 [pipeline-agent (B-001) → whoever picks up T-047 (feature_spec v2 fit scope)]
`tasks/available/T-047-feature-spec-v2-fit-scope.md:130` has a done-when item referencing
`_MOCK_FEATURE_SPEC` in `tests/integration/phases/test_phase_subgraphs_smoke.py`. After B-001 that
constant — and the entire network-free mock set the smoke test used to own — lives in
`tests/fixtures/graph_mocks.py`, shared with `tests/unit/graph/test_checkpointer.py`. Update
`_MOCK_FEATURE_SPEC` there, and note that both consumers now see the change: the checkpointer test
drives `feature_engineer` for real through phase 4, so a payload that `_validate_feature_spec`
rejects will fail that test too, not only the smoke test.
Not fixed here: `tasks/available/` is outside B-001's `folders:`, so the task file itself was left
untouched.
Status: resolved in T-047 — `_MOCK_FEATURE_SPEC` was migrated to schema v2 in
`tests/fixtures/graph_mocks.py` (two entries, one `global` and one matching a family, so both
branches of the fit-scope guard run), and both consumers were verified green.

## OPEN — 2026-08-13 [pipeline-agent (B-001 review) → whoever next touches src/graph/]
**Subgraph resume granularity is finer than the code comments claim: resume is node-granular
*inside* a phase, not phase-atomic.** LangGraph propagates the parent checkpointer into
subgraphs, so a crash mid-phase resumes at the next un-executed node within that phase rather
than restarting the phase. Demonstrated by crashing mid-phase-1 immediately after
`validation_strategist` had frozen `validation/fold_config.json`: on resume
`validation_strategist` did **not** re-run, every phase-1 node count stayed at 1, and CLAUDE.md
invariant #1 (write-once folds) held with no `FoldsAlreadyFrozenError`.
Why this matters: `src/graph/phases/generic.py:31-32` ("Subgraph-level `compile()` takes no
checkpointer/interrupts — those apply only once, at the top level") and the checkpointer test's
own docstring both describe resume in phase-boundary terms. The real behavior is finer *and*
safer — a phase-atomic resume would re-run `validation_strategist` and trip invariant #1. Recorded
so nobody "fixes" the comment's version of the behavior into existence.
Not fixed here: `src/graph/` is outside B-001's `folders:` (tests only), and this is a
documentation/comment accuracy issue, not a defect.
Status: open

## OPEN — 2026-08-13 [pipeline-agent (B-001 review) → infra-agent (owns src/memory/) / whoever picks up CI (T-044)]
**Pre-existing live network egress in `tests/tools/test_rag.py` — 13 tests reach
`huggingface.co:443`.** `RagStore.__init__` calls `build_embedding_function()`
(`src/memory/store.py:87`), which constructs a `SentenceTransformerEmbeddingFunction` and resolves
the model over the network. The tests pass only because the model happens to be cached locally;
on a cold CI runner they hit the network, and with egress blocked they would fail.
**Not a B-001 regression** — confirmed identical on a clean `main`, and `tests/tools/` is arguably
outside the literal "no network calls in unit tests" gate, since `design.md:703` designates
`src/tools/rag` for a real Chroma test instance in the Integration column.
The larger point: **there is currently no automated guard enforcing that gate at all** — which is
precisely how B-001's live Kaggle `401` survived on `main`. A pytest plugin that raises on any
non-loopback `socket.connect`/`socket.create_connection`/`socket.getaddrinfo` during `tests/unit`
would make the rule structural instead of reviewer-dependent. Flagged as a candidate follow-up
task; it belongs with CI (T-044) rather than in a test-only bug fix.
Status: open

## OPEN — 2026-08-13 [pipeline-agent (B-001 review) → whoever next curates the test layout]
**`tests/unit/graph/test_checkpointer.py` is filed as a unit test but runs real subprocesses.**
It drives phases 1-4 of the real graph, spawns real `code_executor` subprocesses and genuinely
trains a `LogisticRegression` (~6.4s for the single resume test). `design.md`'s testing-strategy
table gives `src/graph/` no Unit column at all — Integration + Smoke only.
Largely pre-existing: main's version already invoked the real graph. B-001 deepened it, because
fixing the mock set turned phase 3 from a silently-degrading no-op into a real training run.
Candidate follow-up: relocate the file to `tests/integration/`. **Deliberately not done in B-001** —
the bug is a test-correctness fix, a move would obscure the diff, and `tests/unit/graph/` is the
path named in the bug's `folders:`.
Status: open

## OPEN — 2026-08-14 [pipeline-agent (T-027) → pipeline-agent (T-028 ensemble_specialist, T-029 coder)]
**The NoOp-test re-pointing chain in `tests/unit/nodes/compute/test_specialist_selector.py` is
terminated — T-028 must NOT re-point anything there.** With `timeseries_specialist` landed, all five
real specialist names resolve to real `LLMNode` subclasses, so routing the *real* `resolve_node` to
any of them from a unit test constructs a chat model and attempts a live API call (T-025's review
caught a real `401` this way). T-027 rewrote that block: the NoOp path now runs through a sentinel
`NEVER_LANDING_SPECIALIST = "never_landing_specialist"` that no module will ever implement, and the
selector-level NoOp test patches the module-private `_select_by_signal` so the real `resolve_node`
still runs but can never be handed a real specialist name. Neither test needs re-pointing again.
**T-028 should add only a `test_real_resolve_node_resolves_landed_ensemble_specialist` case.**
Carrying forward the T-025 warning: these test *bodies* merge without conflict markers, so a
concurrent branch must diff the bodies, not merely resolve markers. Verified both ways — with the
provider env vars unset and set to fake values, under a socket-blocking pytest plugin, the file makes
zero network calls; the pre-rewrite version fails under the same guard.
Status: open (advisory for T-028)

## OPEN — 2026-08-14 [pipeline-agent (T-027) → pipeline-agent (T-028, T-029 coder)]
**The T-025 "disjoint vocabularies" hazard is mitigated here, not fixed.** `specialist_selector`
routes on "time series forecasting"/"forecast"/"arima"/"prophet", and its timeseries branch is
checked *first*, so an LSTM or RNN forecasting plan lands on `timeseries_specialist` — whose family
table does not accept `lstm`/`rnn`. That asymmetry is pinned by
`test_unsupported_model_family_raises` rather than papered over: adding neural families here would
duplicate `deep_learning_specialist`'s table and make the routing boundary meaningless. Likewise, a
generic answer ("forecasting", "lag features") still hard-aborts the phase with zero artifacts,
because these nodes have no retry wrapper (Phase 5's `code_critic` targets `coder`, not the
specialists). Generous aliasing plus an explicit prompt is the only mitigation, consistent with all
three landed siblings. The structural fix — a retry/repair wrapper around specialist responses, or a
`normalize_model_family` longest-match rule — remains unowned; it touches the shared contract and
belongs in its own task.
Status: open

## OPEN — 2026-08-14 [pipeline-agent (T-027 review) → pipeline-agent (T-029 coder)]
**The `p-d-q` / `P-D-Q-s` order convention is entirely unvalidated — `coder` must parse it
defensively.** `config/prompts/timeseries_specialist/v1.md` tells the LLM to encode an ARIMA `order`
as `"1-1-1"` and a `seasonal_order` as `"1-1-1-12"`, but nothing checks the shape: `"1,1,1"`,
`"(1,1,1)"`, `"1-1"` (wrong arity), `"1-1-x"` and `"banana"` are all accepted as
`fixed_params["order"]` and written straight through to `design.json`, because
`_validate_fixed_params` only checks that the value is a JSON scalar. T-029 must parse with an
explicit failure path (a clear, attributable error), never `int(part)` on unvalidated input.
Status: open

## OPEN — 2026-08-14 [pipeline-agent (T-027 review) → pipeline-agent (T-029 coder)]
**Two different encodings of a tuple hyperparameter can legitimately reach `design.json`, and the
prompt's stated reason for banning one of them is wrong.** Inside `search_space`, `choices` accepts
JSON scalars only, so an array order genuinely is rejected. Inside `fixed_params`,
`_validate_fixed_params` explicitly permits a *flat list of scalars*, so
`fixed_params: {"order": [1, 1, 1]}` and `{"seasonal_order": [1, 1, 1, 12]}` are ACCEPTED and
written through unchanged (verified). The prompt now states the array ban in `fixed_params` as a
pipeline convention rather than a validator rejection, but **T-029 must handle both encodings**
(hyphenated string and flat list) rather than trusting the convention.
Inherited-wording note: `config/prompts/nlp_specialist/v1.md`'s `ngram_range` section carries the
identical incorrect claim ("`"ngram_range": [1, 2]` is rejected") for the same reason — a flat list
in `fixed_params` is accepted there too. **Not edited here** (landed sibling, outside this task's
change set); recorded so whoever revisits `nlp_specialist` or writes `coder` knows the claim is
false in both prompts.
Status: open

## OPEN — 2026-08-14 [pipeline-agent (T-027 review) → pipeline-agent (T-029 coder) / whoever owns fold integrity]
**The prompt forbids six fold-shaping keys that `FORBIDDEN_CV_KEYS` does not reject — the gap is
prompt-only, and honoring one silently breaks score comparability.** `gap`, `max_train_size`,
`initial`, `horizon`, `period` and `cutoffs` are none of them in `FORBIDDEN_CV_KEYS`, so an LLM that
emits `fixed_params: {"gap": 7}` or `{"max_train_size": 500}` gets a valid `design.json`. If `coder`
(T-029) passes those through to a splitter, that experiment is scored against a **different
effective split** than its siblings while still being written to the same `experiments/` tree — so
`best_score`/`best_experiment_path` comparisons silently stop meaning anything (CLAUDE.md invariants
#1 and #3). Deliberate carve-out T-029 must preserve: `forecast_horizon` as a genuine *model*
parameter (used at fit time by a direct multi-step model) IS permitted — the distinction is whether
the horizon carves a holdout or parameterizes the model, which no schema check can make.
Status: open

## OPEN — 2026-08-14 [pipeline-agent (T-027) → pipeline-agent (T-029 coder)]
**`gradient_boosting_lags` is coarser than `classical_ml_specialist`'s families — `coder` gets
strictly less library information from a timeseries design.** `classical_ml_specialist` keeps
`xgboost`, `lightgbm` and `catboost` as separate canonical families; `timeseries_specialist`
collapses all three (plus the sklearn boosting estimators) into the single
`gradient_boosting_lags` token, because the meaningful distinction for a temporal design is
"boosting over lag features" rather than which boosting library implements it. Consequence: given
`model_family: "gradient_boosting_lags"`, `coder` cannot recover which library the specialist had in
mind and must pick a default (the `rationale` may name one, but it is free text). Recorded rather
than "fixed": splitting the family would multiply the table without changing the design semantics,
and is a decision for whoever specifies `coder`'s dispatch.
Status: open

## OPEN — 2026-08-14 [pipeline-agent (T-027) → whoever next edits `_experiment_design.py`]
**`_experiment_design.normalize_model_family`'s docstring names an example that is no longer true
for every caller.** Its lines 233-236 say `xgb`, `XGBoost`, `light-gbm`, `ExtraTrees` "all resolve".
That holds under `classical_ml_specialist`'s table (T-024), but `ExtraTrees` is deliberately NOT an
alias in `timeseries_specialist`'s table — bagged-tree answers there must raise rather than resolve
to a boosting family (see the T-027 decisions entry). The docstring documents a *caller-supplied*
table, so the claim was always illustrative rather than normative, and it is **deliberately not
edited here**: `_experiment_design.py` is shared by four landed specialists and this task's only
sanctioned touch to it is its module docstring. Flagged so nobody reads it as a guarantee.
## OPEN — 2026-08-14 [pipeline-agent (T-030) → pipeline-agent (whoever owns the `src/nodes/llm/base.py` refactor)]
**The critic retry-guard + verdict-normalization block is now duplicated twice.**
`analysis_critic.py:232-374` and `code_critic.py` carry structurally identical implementations of:
the per-target `retry_counts` guard, the `(max_retries + 1) * max(len(targets), 1)` global cap with
its pigeonhole comment, the forced-pass attempt record, the `target_delta`/`target_messages` merge,
and a `_parse_verdict` that normalizes verdict/target/feedback and never raises. Separately, the
JSON-extraction count is now at **eight** call sites (see the 2026-08-12 T-024 entry above at
`context/discoveries.md:296-312`, which proposes hoisting the extractor trio into `base.py`).
Proposal: extend that same task to also hoist `run_critic_retry_loop(...)` / `parse_verdict(...)`
alongside the extractor trio, and migrate both critics.
Preserve the deliberate differences when merging — they are not accidents:
- `analysis_critic` catches `FoldsAlreadyFrozenError` (scoped to `validation_strategist`);
  `code_critic` deliberately has **no** `try/except` around the target call, because `coder` has no
  write-once guard and a real crash must surface.
- `analysis_critic` overrides `_resolve_output_path` for a `{phase}` placeholder (it runs in two
  phases at the same iteration); `code_critic` runs in one phase and uses the base implementation.
- `code_critic`'s attempt records carry an extra `code_available` flag.
- `code_critic` binds `resolve_node` through the `node_resolver` *module attribute* (B-001);
  `analysis_critic` still uses the import-time form, and its module docstring documents that as its
  unit tests' patch point. A hoist must not silently flip one of them.
Not done in T-030: `src/nodes/llm/base.py` is reserved for that separate refactor task, and
touching it from a node-scoped task means migrating landed modules in a PR scoped to one node.
Status: open

## OPEN — 2026-08-14 [pipeline-agent (T-030) → pipeline-agent (T-027, T-028)]
**Merge-conflict heads-up, same shape as the resolved T-024/T-023 one.** T-030 appends to
`docs/agents.md`'s agent table, `docs/pipeline.md` § Implementation (Phase 5) (a `code_critic`
bullet placed after the last specialist bullet, immediately before `#### The design.json contract`)
and its § Node classification table, and to the tails of `context/decisions.md` and
`context/discoveries.md`. T-027 (`timeseries_specialist`, in progress) and T-028
(`ensemble_specialist`) touch exactly the same five tails. Resolve **keep-both**: the specialist
bullets and the `code_critic` bullet are independent additions, and `code_critic` belongs last in
the § Implementation section because it is the phase's last node.
Status: open

## OPEN — 2026-08-17 [pipeline-agent (T-030) → pipeline-agent (T-029 `coder`, T-031 evaluation)]
**`code_critic` reviews the *last* regenerated experiment but `LabState["experiments"]` keeps
pointing at the *first* one.** The retried target's non-`messages` delta is merged into the node's
local `working_state` (so the next review cycle re-reads the regenerated `train.py`) but the node's
returned delta is `{"messages": ...}` only, by contract. Measured with a `coder` stub that appends a
new experiment path per call: the node reviewed `exp_0 → exp_1 → exp_2 → exp_3`, returned a delta
whose only key was `messages`, and left `LabState["experiments"] == [{"path": "experiments/exp_0"}]`.
`experiments` is a plain `list[dict]` LastValue channel, so nothing downstream repairs it: Phase 6 /
`best_experiment_path` (CLAUDE.md invariant #3) would index the *first* script while the workspace
holds the *last*.
State the contradiction plainly, because only one of the two can be true: either the recorded
experiment path is stable across retries (in which case the local merge in `code_critic.__call__` is
unnecessary) or it moves (in which case the messages-only return delta is lossy). Whoever lands
`coder` should decide which, and say so in `docs/pipeline.md`.
**Deliberately not fixed in T-030**: it is latent while `coder` is a `NoOpNode`, and widening a
node's return delta is a scope change (and would need a `LabState`/reducer discussion, since
`experiments` is LastValue rather than an append reducer).
Status: open

## OPEN — 2026-08-17 [pipeline-agent (T-030) → infra-agent (owns `src/config/`)]
**`config/phases/*.yaml` `max_retries` is unvalidated**, so a negative value silently disables the
critic entirely. `_build_critic_config` in `src/config/loaders.py` does not range-check it; with
`max_retries: -1` a critic's `(max_retries + 1) * len(targets)` cycle budget is `<= 0`, the retry
loop body never executes, and the critic emits a forced `pass` having made **zero** LLM calls and
reviewed nothing. Affects `analysis_critic` equally — both critics derive their budget from this
field. Suggested fix where the config is parsed, not in each critic: reject `max_retries < 0` at load
time with a `ConfigError` naming the phase file.
T-030 hardened the consequence rather than the cause (its `for...else` forced-pass record is now
loop-variable-free and covered by `test_nonpositive_max_retries_forces_pass_without_calling_the_llm`),
and scoped its own unreachability comment to "any input the LLM can currently produce".
Status: open

## OPEN — 2026-08-17 [pipeline-agent (T-030) → pipeline-agent (T-029 `coder`)]
**`code_critic`'s feedback reaches `coder` only through the appended verdict `AIMessage`.** The
prompt (`config/prompts/code_critic/v1.md`) tells the critic its `feedback` is delivered to `coder`
"verbatim", and that is true only insofar as `coder` reads the conversation: the sole channel is the
verdict message appended to `messages`. If T-029's `coder` overrides `_build_messages` without
calling `super()` (or trims to a window that drops the verdict), every retry regenerates a
byte-identical script, the budget burns down, and the forced pass ships the defect **with no test
failing** — the retry loop, the record and the forced pass all still behave correctly.
T-029's Done-when checklist does not currently mention consuming critic feedback; it should assert
that a second `coder` invocation carrying an `iterate` feedback message produces different output.
Status: open

## NOTE — 2026-08-17 [pipeline-agent (T-030) → architecture]
**`AgentConfig.max_tokens` is inert across the whole system.** Every agent YAML carries a
`max_tokens` (it is `_require_field`-enforced by `load_agent_config`), but nothing in `src/` reads
`AgentConfig.max_tokens`: `LLMFactory` configures the model purely from
`Settings.models.{role}.max_tokens`, and `config/settings.yaml`'s `models.implementation` declares
none. So `config/agents/code_critic.yaml`'s `max_tokens: 2048` documents an intent that is not
applied at runtime. Not a T-030 defect — it is architecture-wide and `config/settings.yaml` plus the
`AgentConfig` dataclass are protected contracts — but worth resolving deliberately: either wire
`AgentConfig.max_tokens` through `LLMFactory` or drop the field from the agent-YAML contract. T-030's
task file no longer claims it as a delivered setting.
Status: open

## NOTE — 2026-08-17 [pipeline-agent (T-030) → pipeline-agent (critic refactor)]
**A critic's node-local `messages` list is built by plain concatenation, not by the graph's
reducer.** Both critics maintain `working_state["messages"] = [*previous, response]` while the
compiled graph applies LangGraph's `add_messages` reducer to the channel. The two differ: given two
messages sharing an `id`, `add_messages` yields one (replacement by id) while concatenation yields
two. So the `trim_context` window a critic sees mid-retry is not necessarily the window the same
messages would produce once merged through the channel. Low severity today (mocked and real LLM
responses do not reuse ids, and the window only feeds prompt context), but it is a real divergence
between node-local and graph-level state semantics, and the `base.py` critic hoist proposed above is
the natural place to settle it.
Status: open

## OPEN — 2026-08-17 [pipeline-agent (T-028) → whoever lands the `current_iteration` writer (T-029 coder / T-031 score_evaluator / T-032 iteration loop)]
**`ensemble_specialist`'s `design.json` write is a sharper instance of the already-OPEN
"`current_iteration` never increments" hazard (2026-08-11 T-024 entry above).** That entry
documents every `experiments/exp_{iteration}/design.json` write silently overwriting the previous
iteration's design because nothing increments `state["current_iteration"]`. For the four other
landed specialists that is a self-inflicted loss — an experiment overwrites *its own*
predecessor's design record. For `ensemble_specialist` it is worse: its write also references,
via `base_experiments`, the *other* experiments it combines — and today those base experiments
necessarily share the very same `experiments/exp_{iteration}/design.json` path (per the "one
specialist per iteration" invariant recorded at the 2026-08-12 T-024/T-025 entry above), so
writing the ensemble's own design silently destroys the design record of the base experiment(s) it
just named as sources, in the same write. This is not fixable inside T-028's `folders:`
(`src/nodes/llm/`, `config/agents/`, `config/prompts/`) — it requires the same `current_iteration`
writer already flagged for T-029/T-031/T-032, which must land before an `ensemble_specialist`
design and its base experiments' own designs can coexist on disk. Flagged, not fixed here.

**Addendum (2026-08-18, after the T-028 adversarial review fix): the `current_iteration` writer is
now a hard prerequisite for `ensemble_specialist` running at all, not merely a data-loss hazard.**
The review fix added a duplicate-`oof_path` invariant to `_validate_base_experiments`
(`src/nodes/llm/_experiment_design.py`) — two base experiments resolving to the same OOF file is
rejected outright, because reading one experiment's predictions twice under two labels while
silently dropping another is not a representable ensemble. But while `current_iteration` stays
frozen, *every* experiment `coder` (T-029) writes lands in the same `experiments/exp_0/` directory,
so two fully schema-compliant entries (both carrying a real `id`, `path` and `iteration`, exactly as
`src/state.py` documents) resolve to the same `experiments/exp_0/oof_predictions.parquet` and the
new check raises. Verified by direct execution against the landed code:

```
experiments = [{"id": "exp-0", "path": "experiments/exp_0", "iteration": 0, ...},
               {"id": "exp-1", "path": "experiments/exp_0", "iteration": 0, ...}]
-> ValueError: entries 'exp-0' and 'exp-1' both resolve to the same 'oof_path'
   'experiments/exp_0/oof_predictions.parquet'
```

This is the intended behavior — these nodes have no retry wrapper, so failing loudly and
attributably beats writing a design that double-counts one model and drops another — but whoever
lands the `current_iteration` writer must know that until they do, `ensemble_specialist` cannot
complete a run even once `state["experiments"]` starts being populated. It is not reachable today
(nothing writes `state["experiments"]` yet), so this changes nothing that currently runs.
Status: open

## NOTE — 2026-08-17 [pipeline-agent (T-028) → pipeline-agent (T-029 coder)]
**The OOF-path resolution convention `ensemble_specialist` relies on is binding on `coder`.** For
each `state["experiments"]` entry, `_oof_path_for_experiment` reads that experiment's own
`results.json` and uses its `oof_path` field (a workspace-relative path to the experiment's
out-of-fold predictions) when present and it re-relativizes cleanly; otherwise it falls back to a
well-known `oof_predictions.parquet` file in that same experiment's directory. Nothing in `src/`
writes `results.json` yet — `coder` (T-029) is its only planned producer, and it is currently
blocked — so this convention is speculative until T-029 lands. Whoever implements `coder` must
either write `results.json["oof_path"]` pointing at the real out-of-fold predictions file it
produces, or name that file `oof_predictions.parquet` inside the experiment's own directory (the
fallback `ensemble_specialist` silently assumes when `oof_path` is absent or invalid) — otherwise
every `ensemble_specialist` design references a predictions file that was never written, and
`coder`'s own downstream consumption of that design (fitting a meta-learner on
`base_experiments[i]["oof_path"]`) fails at read time rather than at design time.
Status: open (binding convention for T-029)


## OPEN — 2026-08-17 [pipeline-agent (T-031) → pipeline-agent (T-029 `coder`)]
`score_evaluator`/`feature_importance_extractor` (T-031) pin a `results.json` contract `coder` has not
landed yet, since nothing else in `src/` defines this file's shape beyond `baseline_runner`'s own
`experiments/baseline/results.json` precedent:
- `cv_score` (float, required) — mirrors `baseline_runner.py:126,201`'s own key. `score_evaluator`
  degrades to "no valid score" (never raises) when it is absent, non-numeric, or non-finite.
- `metric` (string, optional) — set it to `"accuracy"`/`"r2"`/`"rsquared"`/`"score"` (separator-
  normalized matching, same as the direction table) to opt an experiment into a `delta_vs_baseline`
  comparison against `state["baseline_score"]`. Any other value, or its absence, just means no
  comparison is made — never an error.
- `feature_importance` (`{feature: value}` dict, optional) and `feature_names` (`list[str]`, optional)
  — read by `feature_importance_extractor` only when `design.json`'s `model_family` is one of the
  curated tree-ensemble tokens. Absent/malformed degrades to a skip artifact, never a raise.

Also flagging that both new nodes inherit `code_critic`'s known experiment-pointer staleness (the
2026-08-17 T-030 entry above, still open): they resolve `state["experiments"][-1]["path"]` via the
same ported helper, which may not track the last-*regenerated* script until `coder` settles which
convention it writes.
Status: open

## NOTE — 2026-08-17 [pipeline-agent (T-031) → pipeline-agent (T-032 iteration loop)]
T-031 explicitly does **not** increment `state["current_iteration"]` — the 2026-08-11
`T-024 → T-031/T-032` entry's "T-031 or T-032" framing resolves to T-032 only. Incrementing it inside
`score_evaluator` (the first Phase 6 node) would desync every `{iteration}`-suffixed artifact the rest
of Phase 6 writes — including `score_evaluator`'s and `feature_importance_extractor`'s own two
artifacts — from the `exp_{N}` experiment directory that iteration actually scored.
Status: open

## RESOLVED — 2026-08-17 [pipeline-agent (T-031)]
Closes the 2026-08-04 `infra-agent (T-002) → pipeline-agent (T-031 score_evaluator)` entry above.
Implemented via `score_evaluator._MINIMIZE_METRICS`/`_direction_for_metric`
(`src/nodes/compute/score_evaluator.py`): every score is normalized to "higher is better" —
minimize-oriented metrics negated via a curated, separator-normalized metric-name match — before it
is ever compared against `best_score` or written to `last_score`/`best_score` anywhere in `LabState`.
`LabState` itself still carries no explicit polarity field; the decision made was to resolve polarity
once, at the single write site, rather than add one (see `context/decisions.md`'s matching 2026-08-17
entry for the full reasoning).
Status: resolved

## OPEN — 2026-08-17 [pipeline-agent (T-031) → whoever owns the `LabState` polarity question (protected
contract, requires human approval)]
Adversarial review of T-031 (repro3) found a real gap in the resolution the entry directly above
records as closed: `best_score` is persisted **already normalized** (sign-flipped for minimize
metrics), with no record anywhere in `LabState` of *which direction produced it*. `score_evaluator`
re-derives direction fresh every call from `problem_definition.json`'s `success_metric`, defaulting to
"maximize" when that file is unset or unreadable (its own degrade-safe convention — necessary so a
missing upstream artifact never crashes the node). Concretely: iteration 0 runs with a readable
`problem_definition.json` declaring `"success_metric": "rmse"`; the actual RMSE of `2.0` (excellent)
normalizes to `best_score = -2.0`. If `problem_definition.json` becomes unreadable on a later
iteration (moved, corrupted, `problem_definition_path` cleared), direction silently defaults to
"maximize" for that call, and a raw RMSE of `50.0` (far worse) is compared as `50.0 > -2.0` — a false
improvement. `best_score` flips to `50.0` and `best_experiment_path` flips to the objectively worse
experiment, with no field anywhere recording that the comparison spanned two different assumed
directions.
The real fix is a polarity field on `LabState` (e.g. persisting `direction` alongside `best_score`, so
a later call can detect a mismatch instead of silently re-assuming "maximize") — `LabState` is a
protected contract per CLAUDE.md and out of T-031's scope to touch without explicit human approval.
**Not mitigated in code**: a mitigation that re-reads a previous iteration's `score_evaluation_*.json`
report to recover the prior direction was considered and deliberately rejected — it would add
cross-iteration file coupling to a compute node for a case the contract should settle properly, not a
targeted fix. What T-031 *does* provide: every `reports/score_evaluation_{iteration}.json` records
`success_metric`, `success_metric_raw`, and `direction` for that call, so a flip like the one above is
at least **forensically detectable** after the fact by diffing consecutive reports, even though nothing
currently detects or blocks it automatically.
Status: open
