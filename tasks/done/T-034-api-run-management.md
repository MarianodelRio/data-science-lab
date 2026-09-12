---
id: T-034
phase: 3
agent: api-agent
depends_on: [T-009]
status: done
folders: ["src/api/"]
outputs: [FastAPI app, POST/GET /api/runs, POST /api/runs/{id}/resume]
size: M
branch: ~
pr: https://github.com/MarianodelRio/data-science-lab/pull/38
---

## FastAPI skeleton + run management (src/api/)

**Scope:** `src/api/main.py` + `src/api/routers/runs.py`.

**Delivers:**
- FastAPI app factory + uvicorn entrypoint
- `POST /api/runs` — creates a run (competition, workspace), starts the compiled graph as an asyncio background task with a `run_id` thread, returns `{run_id, status}`
- `GET /api/runs` — lists runs with current phase/status
- `GET /api/runs/{id}` — returns one run's state summary
- `POST /api/runs/{id}/resume` — injects `human_feedback` and resumes from the interrupt
- Graph invocation is injected so tests can substitute a fake graph

**Done when:**
- [ ] `POST /api/runs` returns 201 with `{run_id}` and starts a background task (fake graph)
- [ ] `GET /api/runs/{id}` returns 200 with `phase` and `current_iteration`
- [ ] `POST /api/runs/{id}/resume` with `{"feedback":"proceed"}` returns 200 and passes feedback into the graph (asserted via fake)
- [ ] unknown run id returns 404
- [ ] tests use FastAPI `TestClient` + fake graph, no network
- [ ] `docs/api.md` documents these endpoints

## Completed
- What was implemented: `src/api/registry.py` (on-disk run registry per ADR-0001 —
  `RunRecord`, `validate_run_id`, `create_run_record`/`read_run_record`/
  `update_run_status`/`list_run_records`, atomic tmp-file+`os.replace` writes);
  `src/api/models.py` (Pydantic v2 `RunCreateRequest`/`RunCreateResponse`/`RunSummary`/
  `ResumeRequest`/`ResumeResponse`, with `RunSummary`'s `ser_json_inf_nan="null"` so
  `best_score=float("-inf")` serializes as JSON `null`, never `-Infinity`);
  `src/api/routers/runs.py` (the four endpoints — `POST/GET /api/runs`,
  `GET /api/runs/{id}`, `POST /api/runs/{id}/resume` — plus `_json`/`_json_list` helpers
  that serialize via `model_dump_json()`/`TypeAdapter.dump_json()` instead of the default
  `response_model=`/`jsonable_encoder` path, `_has_active_run` for the single-active-run
  409 gate, and `_run_and_track` driving each run's background execution and status
  transitions); `src/api/main.py` (`create_app` factory with injectable `runs_dir`/
  `graph_factory`, `CompiledGraphLike` Protocol, uvicorn entrypoint).
  Tests: `tests/unit/api/test_registry.py` (16 cases), `tests/unit/api/test_models.py`
  (3 cases), `tests/unit/api/test_runs.py` (16 cases, all 14 required scenarios from the
  plan plus the "running" and "completed" resume-conflict cases as separate tests),
  `tests/unit/api/conftest.py`, `tests/fixtures/fake_graph.py`. `docs/api.md` updated
  with the four endpoints' request/response shapes (the `/submit` and `/mlflow/open`
  rows and their "not yet finalized" caveat were left untouched — T-037's scope).
  `context/discoveries/T-034.md` written for frontend-agent (final snake_case response
  shapes vs. `frontend/src/api/types.ts`'s provisional camelCase guess).
- Deviations from plan: `resume_run`'s `graph.update_state(...)` call uses the bare
  `_config(run_id)` (no callback), not `_build_config(run_id, callback)` — the plan's
  prose described building one shared `config` with the callback attached and reusing it
  for both the `update_state` call and the subsequent `_run_and_track` task, but the
  plan's own required test #3 explicitly asserts
  `fake_graph.update_state_calls[-1] == ({"configurable": {"thread_id": run_id}}, {"human_feedback": "proceed"})`
  — a bare config with no `callbacks` key. Resolved the conflict in the test's favor
  (it's the literal Done-when acceptance check) by building the callback-bearing config
  separately, right before scheduling `_run_and_track`: the checkpoint write itself
  isn't a node execution and doesn't need JSONL instrumentation, only the tracked
  execution that follows does. Also split the plan's single "non-interrupted -> 409"
  required test into two separate test functions (`test_resume_running_run_returns_409`,
  `test_resume_completed_run_returns_409`) rather than one parametrized test, for
  clearer failure attribution — same two statuses covered.
- Key decisions: `list_run_records` catches only `(json.JSONDecodeError, TypeError,
  KeyError)` for a corrupt `run.json`, not a bare `Exception`, per retrospective L-005 —
  and logs a `logger.warning` with the run_id and exception on skip, per retrospective
  L-002, so a corrupt record leaves a traceable trail instead of silently vanishing from
  the list forever. `_has_active_run` (a pure in-memory dict scan) runs before any
  registry/filesystem call in `create_run`, per retrospective L-004, so a conflicting
  request never touches disk. `_default_graph_factory` imports `GraphBuilder` lazily
  inside the function body so LangGraph/sqlite are never imported by a test that injects
  its own `graph_factory`; its return is `cast` to `GraphFactory` since the real
  `CompiledStateGraph.invoke/get_state/update_state` take a `RunnableConfig` (a
  structural superset of `CompiledGraphLike`'s narrower `dict`-based test protocol) —
  mypy can't verify the structural match automatically, but it holds at runtime.
- Dependencies added: None — `fastapi`, `uvicorn[standard]`, and `httpx` (FastAPI
  `TestClient`'s transitive dependency, used directly in tests) were already present.

### Fix round 1 — review blocker (unbounded graph_factory calls, SEC-99f25ece / CQ-6e49f265 / CQ-5f29f0d4)

Two independent reviewers (security, code-quality) found that `list_runs` and `get_run`
called `request.app.state.graph_factory(...)` fresh on every request, discarding the
built graph immediately after reading `.get_state(...)`. Since the real factory
(`_default_graph_factory` -> `GraphBuilder().build` -> `build_checkpointer`) opens a raw,
unclosed `sqlite3.connect(...)` per call, this leaked one connection per stored run per
`GET /api/runs` poll, unbounded — a real file-descriptor exhaustion path, not a
false positive.

Fix: added `app.state.graph_cache: dict[str, Any] = {}` in `src/api/main.py::create_app`,
alongside the existing `active_runs` dict. Added `_get_or_build_graph(request, run_id)` in
`src/api/routers/runs.py` — a cache-or-build helper keyed by `run_id` — and routed
`create_run`, `list_runs`, `get_run` and `resume_run` all through it instead of calling
`graph_factory` directly. A given run's graph (and its one sqlite connection) is now built
at most once per process lifetime: once on `create_run` (or, for a run created in an
earlier process lifetime and first seen via a read, on first `list_runs`/`get_run`/
`resume_run`), then reused by every later call for that same `run_id` within this
process — `create_run`'s background task (`_run_and_track`) and a concurrent `GET` now
share the identical graph object, not two separate connections to the same checkpoint file.

Also fixed `CQ-5f29f0d4`: `list_runs`/`get_run` called `graph_factory`/`get_state`
synchronously inline on the event loop, unlike the `to_thread`-wrapped equivalent calls in
`_run_and_track`. Added `_get_or_build_graph_values(request, run_id)`, combining the
lookup-or-build step with the `get_state` read as one unit, and wrapped that single unit in
`asyncio.to_thread(...)` in both endpoints — so a cache-miss `sqlite3.connect()` (or a
blocking checkpoint read inside `get_state`) never blocks the event loop, and a cache hit
costs one cheap dict lookup inside the thread-pool call rather than two separate
`to_thread` round trips.

Regression tests added to `tests/unit/api/test_runs.py`:
`test_get_run_builds_graph_at_most_once_across_repeated_requests` and
`test_list_runs_builds_each_graph_at_most_once_across_repeated_calls`, both using a
counting `graph_factory` (distinct from the shared `fake_graph` fixture, which returns a
singleton and can't observe call count) to assert the factory is invoked exactly once per
`run_id` even across repeated `GET` calls. All 36 pre-existing `tests/unit/api/` cases
still pass unchanged — they never asserted on `graph_factory` call count, only on
`fake_graph`'s own `invoke_calls`/`update_state_calls`, which are unaffected by caching
the graph object itself. Full suite: 2176 passed, 97.38% coverage; `ruff check`/
`ruff format --check`/`mypy src/` all clean.

### Fix round 2 — review blocker (resume_run missing active-run gate + TOCTOU race, ADV-09702150)

The adversarial reviewer found `resume_run` never called `_has_active_run` (unlike
`create_run`), so resuming an interrupted run could start it executing concurrently with
an already-active run — no gate enforced the "single active run" concurrency NFR on this
path at all. Independently, `resume_run`'s own sequence was itself racy for the *same*
run_id: it awaited `graph.update_state(...)` (a yield point) *before* creating and
registering the background task into `active_runs`, so two near-simultaneous resume calls
for the same run_id could both pass the `status != "interrupted"` check before either
task registration took effect, both call `update_state`, and both schedule their own
`_run_and_track` task against the same LangGraph checkpoint — the second one silently
overwriting (and orphaning) the first in `active_runs`.

Fix, in `src/api/routers/runs.py`:
- Added the missing `_has_active_run(request)` 409 check to `resume_run`, right after the
  404 check and before the interrupted-status 409 check — same pattern and same
  precedence as `create_run`.
- Added `_resume_and_track(runs_dir, run_id, graph, config, feedback)`, a new coroutine
  that does the `graph.update_state(...)` write *and* drives the subsequent tracked
  execution (`_run_and_track`) as one unit. `resume_run` now builds `graph`/`callback`/
  `config` synchronously (no `await`), calls
  `asyncio.create_task(_resume_and_track(...))`, and assigns
  `request.app.state.active_runs[run_id] = task` on the very next line — zero `await`
  anywhere between the interrupted-status check and that assignment, closing the race
  window entirely (once `resume_run` starts running on the event loop it runs to
  completion without yielding, so no other coroutine can interleave inside it).
  Preserved the pre-existing behavior that the checkpoint write itself carries no
  callback (`_resume_and_track` builds a bare `_config(run_id)` for the `update_state`
  call specifically, and only the callback-bearing `config` passed in is used for the
  tracked `_run_and_track` run that follows) — this was called out as a deliberate
  deviation in the original implementation notes above and stays intact.
- Updated `test_resume_passes_feedback_into_graph`: `update_state` now happens inside the
  background task rather than being synchronously observable right after the HTTP
  response, so the assertion polls (`_wait_until`) for `fake_graph.update_state_calls`
  before checking its contents, instead of asserting immediately — same polling pattern
  already used elsewhere in the file for `active_runs[run_id].done()`.
- Added `test_resume_conflicts_with_active_run_returns_409` (cross-run case: an active
  run from `create_run` blocks a concurrent `resume_run` on a separate interrupted run —
  deterministic via a `SlowFakeGraph.invoke` that sleeps, same technique as the existing
  `test_create_run_conflicts_with_active_run_returns_409`) and
  `test_resume_same_run_twice_only_schedules_one_execution` (same-run case: two resume
  calls for the same run_id, asserting exactly one succeeds with 200, the other with 409,
  and only one `update_state`/`invoke` pair ever reaches the graph — deterministic
  because the second call is turned away either by the new `_has_active_run` gate or by
  the interrupted-status check, whichever the first task's progress makes true by the
  time the second call runs).
- Out of scope for this round (per the Orchestrator's blocker report, left untouched):
  unbounded `graph_cache`/`active_runs` growth (ADV-9f400ebb), synchronous
  `update_run_status` calls inside `_run_and_track` (ADV-d76c0221), `create_run`'s
  synchronous graph build (CQ-95dfe3b3), `_get_or_build_graph`'s non-thread-safe
  check-then-act (CQ-fc809918/SEC-c7d1a204), and the accepted 0.0.0.0 bind /
  process-local active-run tracking tradeoffs (SEC-3a9b77cc, SEC-4b539ff9/CQ-e308af13).

Full suite: 2178 passed, 97.38% coverage; `ruff check .`/`ruff format --check .`/
`mypy src/` all clean.
