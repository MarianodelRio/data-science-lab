---
id: T-034
phase: 3
agent: api-agent
depends_on: [T-009]
status: pr-open
folders: ["src/api/"]
outputs: [FastAPI app, POST/GET /api/runs, POST /api/runs/{id}/resume]
size: M
branch: feature/T-034-api-run-management
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
