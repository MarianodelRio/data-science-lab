"""`GET /api/runs/{id}/events` — Server-Sent Events stream of pipeline
execution events, backed by the per-run queue `EventEmitter` writes to (see
`src/api/event_emitter.py`).

The queue lives on `request.app.state.event_queues` (registered synchronously
in `create_run`/`resume_run`, see `src/api/routers/runs.py`) and is removed
from that dict lazily, here, once this generator consumes the terminal
`STREAM_END` sentinel pushed by `_run_and_track`'s `finally` block.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.api import registry
from src.api.event_emitter import STREAM_END
from src.api.registry import RunNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["events"])

HEARTBEAT_INTERVAL_SECONDS = 15.0


async def _event_stream(
    request: Request, run_id: str, queue: asyncio.Queue[Any] | None
) -> AsyncIterator[str]:
    if queue is None:
        return
    while True:
        if await request.is_disconnected():
            return
        try:
            item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            yield ": heartbeat\n\n"
            continue
        if item is STREAM_END:
            request.app.state.event_queues.pop(run_id, None)
            return
        yield f"data: {json.dumps(item)}\n\n"


@router.get("/{run_id}/events")
async def stream_events(run_id: str, request: Request) -> StreamingResponse:
    runs_dir = request.app.state.runs_dir
    try:
        registry.read_run_record(runs_dir, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    queue = request.app.state.event_queues.get(run_id)
    return StreamingResponse(_event_stream(request, run_id, queue), media_type="text/event-stream")
