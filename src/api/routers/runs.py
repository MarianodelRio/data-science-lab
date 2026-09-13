"""`/api/runs` endpoints — create, list, inspect and resume pipeline runs.

The compiled graph is never imported directly here: `request.app.state.graph_factory`
is the injection seam (see `src/api/main.py`), so unit tests substitute a fake graph
with no LangGraph/sqlite dependency.

Every response is built through `_json`/`_json_list`, which serialize with Pydantic's
own JSON encoder (`model_dump_json()` / `TypeAdapter.dump_json()`) rather than the
`response_model=` + `jsonable_encoder` path FastAPI would otherwise take — the latter
would emit the invalid JSON token `-Infinity` for a fresh run's `best_score`
(`float("-inf")`, see `src/api/models.py`). `response_model=` stays on each decorator
purely so it still shows up in the generated OpenAPI docs.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from langchain_core.callbacks import BaseCallbackHandler
from pydantic import TypeAdapter
from starlette.requests import HTTPConnection

from src.api import registry
from src.api.event_emitter import (
    STREAM_END,
    EventEmitter,
    create_event_queue,
    put_event_dropping_oldest,
)
from src.api.models import (
    ResumeRequest,
    ResumeResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunSummary,
)
from src.api.registry import RunNotFoundError, RunRecord
from src.api.responses import json_response as _json
from src.observability.jsonl_callback import JsonlCallbackHandler
from src.state import LabState, new_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])

_RUN_SUMMARY_LIST_ADAPTER: TypeAdapter[list[RunSummary]] = TypeAdapter(list[RunSummary])


def _config(run_id: str) -> dict[str, Any]:
    """The LangGraph thread config for `run_id` — always nested under
    `"configurable"`, never a bare `{"thread_id": ...}`."""
    return {"configurable": {"thread_id": run_id}}


def _build_config(
    run_id: str, callbacks: list[BaseCallbackHandler] | None = None
) -> dict[str, Any]:
    config = _config(run_id)
    if callbacks:
        config["callbacks"] = callbacks
    return config


def _json_list(items: list[RunSummary], status_code: int = 200) -> Response:
    """Same rationale as `_json`, for the bare-list `GET /api/runs` response."""
    return Response(
        content=_RUN_SUMMARY_LIST_ADAPTER.dump_json(items),
        media_type="application/json",
        status_code=status_code,
    )


def _live_values(graph: Any, run_id: str) -> dict[str, Any]:
    """Read the live `LabState` values from the graph's checkpoint, or `{}`
    if the run has no checkpoint yet (e.g. still `"pending"`)."""
    values = graph.get_state(_config(run_id)).values
    return values if values else {}


def _get_or_build_graph(connection: HTTPConnection, run_id: str) -> Any:
    """Return the cached compiled graph for `run_id`, building it (via
    `graph_factory`) only on first use.

    The real `graph_factory` (`_default_graph_factory` in `src/api/main.py`)
    builds a `CompiledStateGraph` backed by a raw, unclosed sqlite
    connection (`build_checkpointer` in `src/graph/checkpointer.py`).
    Calling it fresh on every request — as `list_runs`/`get_run` used to —
    leaked one sqlite connection per run per request, unbounded. Caching by
    `run_id` on `app.state.graph_cache` bounds this to at most one graph
    (and one connection) per distinct run ever touched by this process,
    and lets `create_run`, `list_runs`, `get_run`, `resume_run` and the
    chat WebSocket handler all share the same graph object for a given run
    instead of each holding its own connection to the same checkpoint file.

    Takes `HTTPConnection` (the common base of `Request` and `WebSocket`)
    rather than `Request` so the chat WebSocket handler in
    `src/api/routers/chat.py` can call it too.
    """
    cache: dict[str, Any] = connection.app.state.graph_cache
    if run_id not in cache:
        cache[run_id] = connection.app.state.graph_factory(run_id, connection.app.state.runs_dir)
    return cache[run_id]


def _get_or_build_graph_values(connection: HTTPConnection, run_id: str) -> dict[str, Any]:
    """Lookup-or-build the graph and read its live state, as one blocking
    unit. Run via `asyncio.to_thread` (like `_run_and_track` already does
    for `invoke`/`get_state`) so a cache-miss `sqlite3.connect()` — or a
    blocking checkpoint read inside `get_state` — never runs on the event
    loop."""
    graph = _get_or_build_graph(connection, run_id)
    return _live_values(graph, run_id)


def _to_summary(record: RunRecord, values: dict[str, Any]) -> RunSummary:
    return RunSummary(
        run_id=record.run_id,
        competition_name=record.competition_name,
        workspace_path=record.workspace_path,
        status=record.status,
        phase=values.get("phase", ""),
        current_iteration=values.get("current_iteration", 0),
        best_score=values.get("best_score", float("-inf")),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _has_active_run(connection: HTTPConnection) -> str | None:
    """Return the first run_id with a not-yet-`.done()` background task, or
    `None` if no run is currently active. This is a cheap in-memory check —
    run it before any filesystem/registry work in `create_run` so a
    conflicting request never touches disk."""
    active_runs: dict[str, asyncio.Task] = connection.app.state.active_runs
    for run_id, task in active_runs.items():
        if not task.done():
            return run_id
    return None


async def _run_and_track(
    runs_dir: Path,
    run_id: str,
    graph: Any,
    config: dict[str, Any],
    initial_state: LabState | None,
    event_queue: asyncio.Queue[Any],
) -> None:
    """Drive one graph execution to completion/interruption/failure and keep
    the on-disk registry status in sync.

    Takes plain values, never a `Request` — this coroutine is scheduled via
    `asyncio.create_task` and outlives the request/response cycle that
    started it, so it must not hold a reference to that request.

    The graph runs arbitrary pipeline node code, so the exception type it can
    raise is genuinely unbounded — a broad `except Exception` here is the
    correct boundary (any failure marks the run `"failed"` and is logged),
    not an accidental swallow of a narrower expected error.

    The `finally` block pushes the terminal `STREAM_END` sentinel on all
    three outcomes (completed/interrupted/failed) so the SSE generator in
    `routers/events.py` always closes. This runs on `_run_and_track`'s own
    frame — the event-loop thread — so it calls `put_event_dropping_oldest`
    directly, not via `call_soon_threadsafe` (only `EventEmitter._emit`,
    called from hooks firing on the worker thread running `graph.invoke`,
    needs that thread-safe hop).
    """
    try:
        registry.update_run_status(runs_dir, run_id, "running")
        await asyncio.to_thread(graph.invoke, initial_state, config)
        snapshot = await asyncio.to_thread(graph.get_state, config)
        status = "interrupted" if snapshot.next else "completed"
        registry.update_run_status(runs_dir, run_id, status)
    except Exception:
        logger.exception("run %s failed during execution", run_id)
        registry.update_run_status(runs_dir, run_id, "failed")
    finally:
        put_event_dropping_oldest(event_queue, STREAM_END)


@router.post("", response_model=RunCreateResponse, status_code=201)
async def create_run(body: RunCreateRequest, request: Request) -> Response:
    if _has_active_run(request) is not None:
        raise HTTPException(status_code=409, detail="another run is already active")

    runs_dir: Path = request.app.state.runs_dir
    run_id = uuid.uuid4().hex
    registry.create_run_record(runs_dir, run_id, body.competition_name, body.workspace_path)

    callback = JsonlCallbackHandler(run_id, runs_dir)
    loop = asyncio.get_running_loop()
    event_queue = create_event_queue()
    request.app.state.event_queues[run_id] = event_queue
    emitter = EventEmitter(run_id, event_queue, loop)

    graph = _get_or_build_graph(request, run_id)
    config = _build_config(run_id, [callback, emitter])
    initial_state = new_state(
        body.competition_name, body.workspace_path, max_iterations=body.max_iterations
    )

    task = asyncio.create_task(
        _run_and_track(runs_dir, run_id, graph, config, initial_state, event_queue)
    )
    request.app.state.active_runs[run_id] = task

    return _json(RunCreateResponse(run_id=run_id, status="pending"), 201)


@router.get("", response_model=list[RunSummary])
async def list_runs(request: Request) -> Response:
    runs_dir: Path = request.app.state.runs_dir
    records = sorted(registry.list_run_records(runs_dir), key=lambda r: r.created_at)

    summaries = []
    for record in records:
        values = await asyncio.to_thread(_get_or_build_graph_values, request, record.run_id)
        summaries.append(_to_summary(record, values))

    return _json_list(summaries)


@router.get("/{run_id}", response_model=RunSummary)
async def get_run(run_id: str, request: Request) -> Response:
    runs_dir: Path = request.app.state.runs_dir
    try:
        record = registry.read_run_record(runs_dir, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    values = await asyncio.to_thread(_get_or_build_graph_values, request, run_id)
    return _json(_to_summary(record, values))


async def _resume_and_track(
    runs_dir: Path,
    run_id: str,
    graph: Any,
    config: dict[str, Any],
    feedback: str,
    event_queue: asyncio.Queue[Any],
) -> None:
    """Write the resume feedback into the checkpoint, then drive execution —
    both steps happen inside this one task so nothing can register a second
    task for this run_id in the gap between them (the TOCTOU race this
    fixes: see `resume_run`, which schedules this task and registers it into
    `active_runs` with zero `await` in between).

    The checkpoint write itself is not a node execution, so — same as
    before this fix — it carries no callback: it uses the bare thread
    config, not `config` (which has the callback attached for the tracked
    run that follows)."""
    await asyncio.to_thread(graph.update_state, _config(run_id), {"human_feedback": feedback})
    await _run_and_track(runs_dir, run_id, graph, config, None, event_queue)


async def do_resume(connection: HTTPConnection, run_id: str, feedback: str) -> ResumeResponse:
    """Validate and kick off a resume for `run_id`, injecting `feedback` as
    `human_feedback` into the paused checkpoint.

    Shared by `POST /api/runs/{id}/resume` (`resume_run`, below) and the chat
    WebSocket handler's `"approve"`/`"redirect"` frames (`src/api/routers/chat.py`)
    — one implementation for both callers, per
    `docs/adr/0002-explainer-is-not-an-llmnode.md`'s decision that forwarding
    a human interrupt decision is not the explainer's job.

    Takes `HTTPConnection` (the common base of `Request` and `WebSocket`) so
    both callers can pass their own connection object through unchanged.

    Note on the TOCTOU race this guards: see the module comment above
    `_resume_and_track`.

    `read_run_record` and `_get_or_build_graph` both do blocking I/O
    (registry file read; on a cache miss, `sqlite3.connect()`), so both run
    via `asyncio.to_thread` — same rationale as `_get_or_build_graph_values`
    above. They run *before* the `_has_active_run`/interrupted-status checks
    rather than between the checks and `active_runs` registration: an
    `await` in that gap would reopen the TOCTOU race the zero-await sequence
    below exists to close. Neither call depends on the checks' outcome (a
    404 or 409 below simply discards the already-built `record`/`graph`),
    so reordering them earlier costs nothing but an occasional wasted
    lookup on the error paths.
    """
    runs_dir: Path = connection.app.state.runs_dir
    try:
        record = await asyncio.to_thread(registry.read_run_record, runs_dir, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    graph = await asyncio.to_thread(_get_or_build_graph, connection, run_id)

    if _has_active_run(connection) is not None:
        raise HTTPException(status_code=409, detail="another run is already active")

    if record.status != "interrupted":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id!r} is not interrupted (status={record.status!r})",
        )

    # The checkpoint write (inside `_resume_and_track`) is not a node
    # execution, so it carries no callback — only the subsequent tracked run
    # is instrumented.
    callback = JsonlCallbackHandler(run_id, runs_dir)
    loop = asyncio.get_running_loop()
    # A fresh queue every resume, even if a stale one from the prior run of
    # this run_id is still sitting in `event_queues` — a client still
    # streaming the old queue object will not see this run's events
    # (accepted: SSE is an explicitly lossy live view, not a durable
    # channel; see context/decisions/T-035.md).
    event_queue = create_event_queue()
    connection.app.state.event_queues[run_id] = event_queue
    emitter = EventEmitter(run_id, event_queue, loop)
    config = _build_config(run_id, [callback, emitter])

    # No `await` from here to the `active_runs` registration below: this is
    # what closes the same-run TOCTOU race (two concurrent resume calls could
    # otherwise both pass the checks above before either task registration
    # took effect). The checkpoint write itself now happens inside the task.
    task = asyncio.create_task(
        _resume_and_track(runs_dir, run_id, graph, config, feedback, event_queue)
    )
    connection.app.state.active_runs[run_id] = task

    return ResumeResponse(run_id=run_id, status="running")


@router.post("/{run_id}/resume", response_model=ResumeResponse)
async def resume_run(run_id: str, body: ResumeRequest, request: Request) -> Response:
    result = await do_resume(request, run_id, body.feedback)
    return _json(result)
