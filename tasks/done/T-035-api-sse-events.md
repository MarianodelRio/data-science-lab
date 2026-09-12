---
id: T-035
phase: 3
agent: api-agent
depends_on: [T-034]
status: done
folders: ["src/api/"]
outputs: [GET /api/runs/{id}/events SSE stream, asyncio.Queue event emitter]
size: M
branch: feature/T-035-api-sse-events
pr: https://github.com/MarianodelRio/data-science-lab/pull/39
---

## SSE event stream + event emitter (src/api/)

**Scope:** `src/api/routers/events.py` + an event-emitter used by the pipeline.

**Delivers:**
- An `EventEmitter` backed by a per-run `asyncio.Queue`; the pipeline pushes events `{phase, node, event, timestamp, summary}`
- `GET /api/runs/{id}/events` — Server-Sent Events endpoint streaming that queue to the browser
- Clean disconnect handling (client gone → stop without error); heartbeat to keep the connection alive

**Done when:**
- [x] pushing an event to a run's queue results in one SSE `data:` frame delivered to a test client
- [x] events preserve order
- [x] client disconnect stops the generator without raising
- [x] the emitter is safe to call from the pipeline background task
- [x] tests use `TestClient`/httpx streaming, no network
- [x] `docs/api.md` documents the SSE event schema

## Completed

Implemented per the Planner's plan, no deviations:

- `src/api/event_emitter.py` (new): `EventEmitter`, a `BaseCallbackHandler` that pushes one
  dict per graph node start/end onto a per-run `asyncio.Queue`, plus `create_event_queue`,
  `put_event_dropping_oldest` (maxsize=1000, drops oldest on full, never raises), and the
  `STREAM_END` sentinel. Independently reimplements `JsonlCallbackHandler`'s node/phase
  identification logic rather than depending on its private members (see
  `context/discoveries/T-035.md`).
- `src/api/routers/events.py` (new): `GET /api/runs/{id}/events` SSE endpoint. `404` for an
  unknown run; streams `data:` frames from the run's queue in order; sends a `: heartbeat`
  comment every `HEARTBEAT_INTERVAL_SECONDS` (15s) while idle; checks
  `request.is_disconnected()` each loop iteration and returns cleanly on disconnect; removes
  the queue from `app.state.event_queues` once it consumes the terminal `STREAM_END` sentinel.
- `src/api/routers/runs.py`: `_build_config` generalized to accept a list of callbacks;
  `_run_and_track`/`_resume_and_track` now thread an `event_queue` param and push `STREAM_END`
  in a `finally` block on all three outcomes; `create_run`/`resume_run` create the queue and
  an `EventEmitter` synchronously (before `asyncio.create_task`) and attach both
  `JsonlCallbackHandler` and `EventEmitter` as callbacks.
- `src/api/main.py`: registers `app.state.event_queues` and the new `events_router`.
- `docs/api.md`: filled in the `GET /api/runs/{id}/events` section with the event schema and
  delivery semantics.
- Tests: `tests/unit/api/test_event_emitter.py` (13 tests — same-thread and real
  cross-thread `call_soon_threadsafe` delivery, LangGraph node filtering, phase derivation,
  defensive fallbacks, never-raise safety, `put_event_dropping_oldest` drop-oldest behavior),
  `tests/unit/api/test_events.py` (10 tests — 404, ordering, disconnect, full lifecycle +
  queue removal, resume creates a fresh queue, heartbeat), and 3 new tests appended to
  `tests/unit/api/test_runs.py` covering the two-callback attachment and synchronous queue
  registration.

**Non-obvious decision:** Starlette's `TestClient` (0.37.2) runs the whole ASGI call
synchronously and only returns once the response is fully buffered — there is no real
incremental/streaming read through it, even via `client.stream(...)` with an early context
exit (confirmed by reading the installed library source after two tests hung indefinitely).
The client-disconnect and heartbeat tests in `test_events.py` therefore drive
`events._event_stream` directly with a minimal fake `Request`, instead of going through
`TestClient` — see `context/decisions/T-035.md` for the full entry. No production code was
affected by this; every other test still goes through the full HTTP stack via `client.get`.

Verification: `pytest --cov=src --cov-fail-under=70 -x` → 2201 passed, 97.39% coverage.
`ruff check . && ruff format --check .` → clean. `mypy src/` → no issues in 85 source files.
