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
from pydantic import BaseModel, TypeAdapter

from src.api import registry
from src.api.models import (
    ResumeRequest,
    ResumeResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunSummary,
)
from src.api.registry import RunNotFoundError, RunRecord
from src.observability.jsonl_callback import JsonlCallbackHandler
from src.state import LabState, new_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["runs"])

_RUN_SUMMARY_LIST_ADAPTER: TypeAdapter[list[RunSummary]] = TypeAdapter(list[RunSummary])


def _config(run_id: str) -> dict[str, Any]:
    """The LangGraph thread config for `run_id` — always nested under
    `"configurable"`, never a bare `{"thread_id": ...}`."""
    return {"configurable": {"thread_id": run_id}}


def _build_config(run_id: str, callback: JsonlCallbackHandler | None = None) -> dict[str, Any]:
    config = _config(run_id)
    if callback is not None:
        config["callbacks"] = [callback]
    return config


def _json(model: BaseModel, status_code: int = 200) -> Response:
    """Serialize `model` via Pydantic's own JSON encoder, bypassing
    `jsonable_encoder`/stdlib `json.dumps` (which would emit invalid
    `-Infinity` for a `-inf` `best_score`)."""
    return Response(
        content=model.model_dump_json(), media_type="application/json", status_code=status_code
    )


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


def _has_active_run(request: Request) -> str | None:
    """Return the first run_id with a not-yet-`.done()` background task, or
    `None` if no run is currently active. This is a cheap in-memory check —
    run it before any filesystem/registry work in `create_run` so a
    conflicting request never touches disk."""
    active_runs: dict[str, asyncio.Task] = request.app.state.active_runs
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


@router.post("", response_model=RunCreateResponse, status_code=201)
async def create_run(body: RunCreateRequest, request: Request) -> Response:
    if _has_active_run(request) is not None:
        raise HTTPException(status_code=409, detail="another run is already active")

    runs_dir: Path = request.app.state.runs_dir
    run_id = uuid.uuid4().hex
    registry.create_run_record(runs_dir, run_id, body.competition_name, body.workspace_path)

    callback = JsonlCallbackHandler(run_id, runs_dir)
    graph = request.app.state.graph_factory(run_id, runs_dir)
    config = _build_config(run_id, callback)
    initial_state = new_state(
        body.competition_name, body.workspace_path, max_iterations=body.max_iterations
    )

    task = asyncio.create_task(_run_and_track(runs_dir, run_id, graph, config, initial_state))
    request.app.state.active_runs[run_id] = task

    return _json(RunCreateResponse(run_id=run_id, status="pending"), 201)


@router.get("", response_model=list[RunSummary])
async def list_runs(request: Request) -> Response:
    runs_dir: Path = request.app.state.runs_dir
    records = sorted(registry.list_run_records(runs_dir), key=lambda r: r.created_at)

    summaries = []
    for record in records:
        graph = request.app.state.graph_factory(record.run_id, runs_dir)
        values = _live_values(graph, record.run_id)
        summaries.append(_to_summary(record, values))

    return _json_list(summaries)


@router.get("/{run_id}", response_model=RunSummary)
async def get_run(run_id: str, request: Request) -> Response:
    runs_dir: Path = request.app.state.runs_dir
    try:
        record = registry.read_run_record(runs_dir, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    graph = request.app.state.graph_factory(run_id, runs_dir)
    values = _live_values(graph, run_id)
    return _json(_to_summary(record, values))


@router.post("/{run_id}/resume", response_model=ResumeResponse)
async def resume_run(run_id: str, body: ResumeRequest, request: Request) -> Response:
    runs_dir: Path = request.app.state.runs_dir
    try:
        record = registry.read_run_record(runs_dir, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if record.status != "interrupted":
        raise HTTPException(
            status_code=409,
            detail=f"run {run_id!r} is not interrupted (status={record.status!r})",
        )

    graph = request.app.state.graph_factory(run_id, runs_dir)
    # The checkpoint write itself is not a node execution, so it carries no
    # callback — only the subsequent tracked run (below) is instrumented.
    # A real sqlite write via the checkpointer — wrap in `to_thread` like the
    # `invoke`/`get_state` calls in `_run_and_track`.
    await asyncio.to_thread(graph.update_state, _config(run_id), {"human_feedback": body.feedback})

    callback = JsonlCallbackHandler(run_id, runs_dir)
    config = _build_config(run_id, callback)
    task = asyncio.create_task(_run_and_track(runs_dir, run_id, graph, config, None))
    request.app.state.active_runs[run_id] = task

    return _json(ResumeResponse(run_id=run_id, status="running"))
