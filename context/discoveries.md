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

## OPEN — 2026-08-10 [Orchestrator (/orchestrate T-019) → pipeline-agent/infra-agent (whoever owns src/graph/ checkpointing)]
While verifying T-019 (`memory_manager`), `tests/unit/graph/test_checkpointer.py::test_resume_after_restart_does_not_rerun_completed_phase` failed on a clean checkout of `origin/main` (verified via a separate clone, independent of any T-019 change): `assert call_counts.get("data_analyst", 0) == 1` fails with `data_analyst` actually called 4 times. This means resuming a run from a checkpoint after a restart currently re-runs `data_analyst` (and likely the rest of phase1) multiple times instead of skipping the already-completed phase — a real bug in the checkpointer/resume path, not a flaky test.
Not fixed here: `src/graph/` is outside T-019's `folders:` (`src/nodes/llm/`, `config/agents/`, `config/prompts/`), and the fix likely requires understanding `src/graph/checkpointer.py`'s interrupt/resume wiring, not a one-line change.
Status: open

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
Status: open
