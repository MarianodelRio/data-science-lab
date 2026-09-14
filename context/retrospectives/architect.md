# Retrospective Memory — Architect
<!-- max 25 entries; prune lowest-weight (oldest on tie) when exceeded -->
<!-- Weight: 3 = cross-module/architectural, 2 = design/planning, 1 = implementation detail -->

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

## L-016 | T-036 | 2026-09-13 | Weight: 3
**Folders:** src/api/
**Lesson:** A handler that captures a status/state snapshot before running slow sequential async setup work, then gates a client-visible decision on that snapshot at the end, has a real staleness race — re-check the status fresh immediately before acting on it, not from the pre-setup snapshot. This generalizes to any future live-data endpoint with an async session-build step between connect and first decision.
**Signal:** "the automatic checkpoint frame was gated on record.status captured before _build_explainer_session ran. That helper does several sequential asyncio.to_thread calls (graph lookup — a cache-miss sqlite3.connect(), WorkspaceManager construction, RAG store factory — can build a real Chroma client) that take real, unbounded wall-clock time, so record.status could be stale by the time the checkpoint-frame decision ran" *(source: ## Completed)*

## L-017 | T-037 | 2026-09-13 | Weight: 3
**Folders:** src/api/
**Lesson:** When ruling that a task must reimplement a thin subset of another agent's private logic instead of importing it (to respect a folder-ownership boundary), require an open discovery documenting the resulting duplication and a concrete trigger for promoting it to a shared module — don't let the boundary-respecting call silently create an unrecorded second implementation of the same shape.
**Signal:** "The two implementations now duplicate this shape (not the code — the node's version also handles a previous-iteration fallback the API route deliberately does not use, see decision #4)." *(source: context/discoveries)*

## L-018 | T-043 | 2026-09-13 | Weight: 3
**Folders:** docker/, ., frontend/
**Lesson:** When a task turns a design.md diagram/example into real running infrastructure (e.g. Docker Compose making a previously-conceptual service topology actually execute), re-check design.md's stated assumptions against the new runtime reality — a diagram can carry a latent scoping gap (e.g. multi-tenant/per-competition isolation) that only becomes a real bug once the thing it describes actually runs.
**Signal:** "the resulting behavior contradicts the per-competition design intent now that it is a real running system instead of a diagram in a doc." *(source: context/discoveries)*

## L-019 | T-040 | 2026-09-13 | Weight: 3
**Folders:** frontend/
**Lesson:** When a shared state field's server-side seed value is a real, legitimate reading (e.g. `0.0`) rather than an unambiguous sentinel (e.g. `-inf`/`None`), a consumer cannot treat that seed as "not yet populated" — require the consumer to use `null`/`undefined` as the sole "unpopulated" signal at the type boundary, never a default equal to a value the server can also genuinely produce.
**Signal:** "`src/state.py:77` seeds `baseline_score=0.0` (not `-inf`, unlike `best_score`), so server-side \"no baseline has run yet\" and \"the baseline genuinely scored 0.0\" are indistinguishable. A component that treats a missing baseline as 0.0 renders a fabricated delta equal to the raw CV score for every row." *(source: context/decisions)*

## L-021 | T-044 | 2026-09-13 | Weight: 3
**Folders:** .github/, README.md
**Lesson:** Before letting a task provision a CI service container for a dependency design.md describes as tested against a real instance, verify some test in the repo actually connects to it programmatically — an aspirational testing-strategy sentence is not evidence the wiring exists, and an unexercised service container is cost plus a false coverage signal.
**Signal:** "Provisioning a GitHub Actions service container would therefore start a Chroma nobody dials." *(source: context/decisions)*

## L-023 | T-044 | 2026-09-13 | Weight: 3
**Folders:** .github/, README.md
**Lesson:** CLAUDE.md's protected-contracts clause governs *changes* to an already-established contract, not the designated task that brings it into existence for the first time (e.g. a `batteries: true`-driven scaffold task) — reading it as requiring separate approval for the creation itself would make every battery task unexecutable; the standard Phase 1 checkpoint is the approval.
**Signal:** "that reading makes every battery task unexecutable." *(source: context/decisions)*

## L-024 | T-041 | 2026-09-13 | Weight: 3
**Folders:** frontend/
**Lesson:** Before approving a task's scope on the strength of spec.md's "out of scope / not yet built" claim for a dependency module, verify that claim against `tasks/done/` and the actual repo state — spec.md can silently lag behind merged work until a `/refine` pass catches up, and an unrevisited stale section can misdirect a task into repeating an already-obsolete pattern (e.g. building a presentational-only component because a dependency API "doesn't exist" when it has, in fact, already shipped).
**Signal:** "`spec.md`'s \"API backend (`src/api/`)\" section states `src/api/` \"contains only an empty `__init__.py`\" and lists T-034–T-037 as not yet built; in reality all four are merged and `src/api/routers/{chat,runs,events,kaggle,mlflow}.py` exist." *(source: context/decisions)*

## L-026 | T-042 | 2026-09-13 | Weight: 3
**Folders:** frontend/
**Lesson:** When approving a task that both corrects a stale API contract (a client type or method) and builds new UI code against that contract, require the correction to land in its own commit before any dependent code is written — so the type-checker enforces the fix on the new code rather than the new code merely encoding an assumption that could still be wrong.
**Signal:** "Reconciling first means the ActionBar is written once, against a type that is already true, and the type-checker becomes an ally rather than a rubber stamp." *(source: context/decisions)*

## L-027 | T-048 | 2026-09-14 | Weight: 3
**Folders:** src/config/, pyproject.toml, docs/configuration.md
**Lesson:** Before approving a task, verify its stated problem premise against the current code — a task's investigation-time description of a bug or gap can have been partially or wholly resolved by the time it's picked up, turning what reads as "add a missing check" into "the check already exists, but nothing calls it at the right time."
**Signal:** "both halves of the task rest on premises that are partly false against the current code." *(source: context/decisions)*

## L-028 | T-051 | 2026-09-14 | Weight: 3
**Folders:** src/state.py
**Lesson:** When a task's Done-when checklist crosses into another agent's owned file, deferring the item to an already-identified downstream task (rather than widening `folders:`, L-022's default) is the right call only if the current doc stays operatively true in the interval — verify the stale-sounding sentence still describes real behavior until the downstream task lands, not just that a downstream task exists to eventually fix it.
**Signal:** "The § State passage that needs rewriting (\"the state contract itself has no polarity field, so `score_evaluator` is responsible for sign-flipping\") stays *operatively true* after T-051 — the field exists but nothing writes or reads it until T-052 — so deferring the doc edit to T-052 leaves no interval of incorrect documentation." *(source: context/decisions)*

## L-029 | T-051 | 2026-09-14 | Weight: 3
**Folders:** src/state.py
**Lesson:** A protected state contract cannot enforce a "write-once" or "set-once" invariant at the type level (TypedDict + LangGraph's LastValue channel accept any last write) — when approving a task that adds such a field, require the contract to document the invariant only and point enforcement at whichever node becomes the field's sole writer, matching the existing `validation_config_path`/`ValidationStrategistNode` precedent rather than inventing guard code the state module structurally cannot host.
**Signal:** "A TypedDict cannot make a field write-once; `validation_config_path`'s immutability is likewise enforced in `ValidationStrategistNode`, not in the state contract. T-051 can deliver the field plus a docstring stating the contract; the enforcement lives in `score_evaluator` (T-052)." *(source: context/decisions)*

## L-030 | T-049 | 2026-09-14 | Weight: 3
**Folders:** src/api/
**Lesson:** When approving a task that wires a new startup-time check into a shared app factory (e.g. `create_app`), require an injectable seam with a test-safe default before allowing a literal call — `TestClient(app)` triggers the ASGI lifespan for every test that constructs the app, so an unconditional check silently turns the whole existing test suite red across every call site, not just the fixture.
**Signal:** "`src/api/main.py:173` runs `app = create_app()` at import time, and `tests/unit/api/conftest.py:51` enters `with TestClient(app)`, which executes the ASGI lifespan for every API unit test. CI ... sets no `ANTHROPIC_API_KEY`/`DEEPSEEK_API_KEY`/`GROQ_API_KEY`/`KAGGLE_*` env vars and no conftest provides them, so an un-injectable validator on either path turns the whole `tests/unit/api/` suite red." *(source: context/decisions)*

## L-031 | T-049 | 2026-09-14 | Weight: 3
**Folders:** src/api/
**Lesson:** When an API endpoint exposes data another module already writes (e.g. pipeline-produced `LabState` fields), rule that it must pass the data through unchanged rather than reshaping, renumbering or deduplicating it at the API boundary — repairing upstream data in a read-only endpoint both risks breaking other consumers' assumptions about that data's identity and hides a real pipeline bug instead of surfacing it.
**Signal:** "Rewriting them in the API would break the `id` ↔ `experiments/exp_{N}/` directory correspondence that `ensemble_specialist`, `error_analyst` and `_experiment_design` rely on, and would put the API in the business of repairing pipeline state it does not own." *(source: context/decisions)*

## L-032 | T-050 | 2026-09-14 | Weight: 3
**Folders:** frontend/
**Lesson:** A reconciliation/type-fixing task's own claimed list of what's wrong can be both over- and under-inclusive — verify every claimed mismatch, and the parts it doesn't mention, against the actual producer source (backend response models, the sole writer of the data) rather than trusting the task file's framing; a wrong return type or a stale request-body key can compile clean and hide a live runtime bug behind a green build.
**Signal:** "the Sidebar would read `.competition_name` off a two-field object and render `undefined` with a green build. Found by reading `src/api/models.py` directly rather than trusting the task's mismatch list." *(source: context/decisions)*
