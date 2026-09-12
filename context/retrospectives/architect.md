# Retrospective Memory — Architect
<!-- max 25 entries; prune lowest-weight (oldest on tie) when exceeded -->
<!-- Weight: 3 = cross-module/architectural, 2 = design/planning, 1 = implementation detail -->

## L-001 | T-032 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** A state field that many modules read but no module writes is a latent pipeline-wide bug, not a gap to fill later — assign the writer to a named task as soon as the asymmetry is spotted, and check which already-landed modules are silently broken by it.
**Signal:** "Before this, every `{iteration}`-suffixed artifact (`design/iteration_{N}/solution_plan.json`, `feature_spec.json`, `experiments/exp_{N}/design.json`, `reports/score_evaluation_{N}.json`) overwrote its predecessor forever, and the already-landed `ensemble_specialist` could not run at all" *(source: context/decisions)*

## L-002 | T-032 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** "Legal under the module DAG" is not sufficient grounds to allow an import — check whether a prior task deliberately decoupled the two modules, and prefer consuming the sibling's output over importing or copying its logic.
**Signal:** "Importing `src/nodes/compute/_evaluation_common.py` from an LLM node is legal under invariant #8 but contradicts T-031's documented ported-not-imported decoupling, and a fresh copy of `resolve_output_iteration`/`candidate_experiment_dirs` could reintroduce the experiment-directory mislabeling bug T-031's adversarial review fixed." *(source: context/decisions)*

## L-003 | T-032 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** Two modules that name the same artifact family by different rules will diverge silently — when approving a task that reads a sibling's output, verify both sides derive the filename from the same source of truth.
**Signal:** "**The two artifact-numbering schemes inside Phase 6 can disagree.**" *(source: context/discoveries)*

## L-004 | T-032 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** Approving a task that ships producers with no consumer is legitimate incremental delivery, but the missing read side must be logged as an open discovery in the same PR or it will never be wired.
**Signal:** "**The three Phase 6 LLM artifacts have no in-code consumer.**" *(source: context/discoveries)*

## L-005 | T-033 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, src/nodes/compute/, config/agents/, config/prompts/
**Lesson:** Approving a consumer whose producer does not exist is the mirror of shipping a producer with no consumer — check the named input against the producing task's declared outputs, because a filename the consumer pins is one the producer does not yet know about.
**Signal:** "as things stand every real run will take the "no submission file" degrade path and nothing will ever be submitted." *(source: context/discoveries)*

## L-006 | T-033 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, src/nodes/compute/, config/agents/, config/prompts/
**Lesson:** Routing data to a workspace artifact because the state contract is protected also routes it out of the checkpointer — say so explicitly when approving, since a resumed run cannot recover what never entered state.
**Signal:** "Nothing in `LabState`, and therefore nothing in the SQLite checkpointer, records whether a run submitted or what it scored." *(source: context/discoveries)*

## L-007 | T-047 | 2026-08-20 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** Before approving a validator that rejects rather than coerces, check whether anything catches the exception — if nothing does, over-matching kills the run while under-matching is merely a covered gap, so the two error directions are not symmetric and "be conservative" is the wrong default.
**Signal:** "A false positive therefore **aborts the Phase 4 run on a correct response**." *(source: context/decisions)*

## L-008 | T-047 | 2026-08-20 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** When two artifacts end up describing the same property, do not widen the older contract to match — align the vocabularies by construction and rule which one wins, so the downstream consumer gets a tie-break instead of a judgement call.
**Signal:** "Where the two artifacts disagree, **`feature_spec.json`'s `fit_scope` is authoritative**." *(source: context/decisions)*

## L-009 | T-029 | 2026-08-31 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** Before treating an open discovery as still-current context for a new task's analysis, check whether a task that landed since it was written already resolved it — an unrevisited discovery can misdirect a scope decision on the very task it names as the follow-up.
**Signal:** "Open discovery \"nothing in `src/` increments `current_iteration`\" is **stale**: T-032's `experiment_designer._build_output_state` is now the single writer." *(source: context/decisions)*

## L-010 | T-029 | 2026-08-31 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** When a node's own internal retry loop can re-invoke against a state field with no LangGraph reducer, require an explicit, documented ruling on what gets overwritten in place versus what gets recorded in state — don't leave a no-reducer-plus-retry interaction implicit for the Coder to improvise.
**Signal:** "Experiment-directory stability across `code_critic` retries must be an explicit, documented decision. Ruling: **overwrite `experiments/exp_{current_iteration}/` in place** on every retry, and record the *directory* (not a file) in `state[\"experiments\"][-1][\"path\"]`." *(source: context/decisions)*

## L-011 | T-029 | 2026-08-31 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** A downstream consumer of a no-reducer state field can silently diverge from disk if it trusts a cached scalar instead of re-reading through the field's own path pointer — when approving a task, check that every consumer of that field follows the same re-read discipline, not just the ones that happen to already do.
**Signal:** "`score_evaluator` is unaffected — confirmed it re-reads `results.json` fresh from disk via each entry's `path` pointer, never trusting the cached `cv_score`. `report_writer` does not follow that same pattern: it trusts the cached field directly." *(source: context/discoveries)*

## L-012 | T-034 | 2026-09-12 | Weight: 3
**Folders:** src/api/
**Lesson:** An implementation can satisfy every literal acceptance criterion in the task file while still violating a stated NFR — when approving a task, check its likely shortcut implementation against the NFRs explicitly, not just against the "Done when" checklist.
**Signal:** "an in-memory dict passes every acceptance criterion while voiding design.md's Availability NFR — a checkpoint that survives restart becomes unreachable because no endpoint lists it" *(source: context/decisions)*

## L-013 | T-034 | 2026-09-12 | Weight: 3
**Folders:** src/api/
**Lesson:** When a task becomes the first code path able to reach an already-landed but never-called module, require it to wire that module up now rather than deferring — the module stays permanently dead if the one task capable of activating it doesn't.
**Signal:** "T-012 landed the handler with **no caller anywhere in `src/`**. The API is the only code path that ever holds a `run_id` at invocation time, so if T-034 does not attach it, `execution.jsonl` is never written, the module stays dead, and T-035 has no on-disk event source to fall back on." *(source: context/decisions)*

## L-014 | T-035 | 2026-09-12 | Weight: 3
**Folders:** src/api/
**Lesson:** When a component's data source runs via `asyncio.to_thread` (a pipeline invocation driven off the event loop), any new consumer that needs to push that data back onto an event-loop-owned structure (`asyncio.Queue`, a WebSocket send, etc.) must be required to use `loop.call_soon_threadsafe` for every write — a bare same-object call from the worker thread is unsafe and fails silently, so a same-thread-only unit test will not catch the regression. This ruling should be inherited by T-036 (WebSocket chat) and any later live-data endpoint rather than rediscovered.
**Signal:** "a bare `put_nowait` from a non-owning thread is unsafe on `asyncio.Queue` and fails silently, undetected by a same-thread unit test" *(source: context/decisions)*
