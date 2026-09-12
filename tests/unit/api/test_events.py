"""Unit tests for `GET /api/runs/{id}/events`, via FastAPI `TestClient` +
`FakeCompiledGraph`. Zero network calls, zero LangGraph/sqlite dependency.

Starlette's `TestClient` (0.37.2, pinned here) runs the whole ASGI call
synchronously inside `portal.call(...)` and only returns to httpx once the
response is fully buffered (see `starlette.testclient._TestClientTransport.
handle_request`) — there is no true incremental/streaming read available
through it, even via `client.stream(...)`. The disconnect and heartbeat
tests below therefore drive `events._event_stream` directly (an async
generator) with a minimal fake `Request`, rather than going through
`TestClient` — the more precise unit boundary for those two behaviors
anyway. Every other test here exercises the full HTTP stack via `client.get`
since it only needs the fully-buffered response body.
"""

import asyncio
import json
import time
from typing import Any

from fastapi.testclient import TestClient

from src.api import registry
from src.api.event_emitter import STREAM_END, EventEmitter, create_event_queue
from src.api.routers import events
from src.observability.jsonl_callback import JsonlCallbackHandler
from tests.fixtures.fake_graph import FakeCompiledGraph


class _FakeRequest:
    """Minimal stand-in for the `Request` param `_event_stream` needs:
    `.app.state.event_queues` and an `is_disconnected()` that flips to
    `True` after `disconnected_after` calls (never, if `None`)."""

    def __init__(self, app: Any, disconnected_after: int | None = None) -> None:
        self.app = app
        self._disconnected_after = disconnected_after
        self._calls = 0

    async def is_disconnected(self) -> bool:
        self._calls += 1
        if self._disconnected_after is None:
            return False
        return self._calls > self._disconnected_after


_POLL_TIMEOUT_SECONDS = 1.0
_POLL_INTERVAL_SECONDS = 0.01


def _wait_until(predicate, timeout: float = _POLL_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL_SECONDS)
    return predicate()


def _create_run(client: TestClient, **overrides) -> dict:
    body = {"competition_name": "titanic", "workspace_path": "/workspace/titanic", **overrides}
    response = client.post("/api/runs", json=body)
    assert response.status_code == 201
    return response.json()


def _wait_for_run_task_done(app, run_id: str) -> None:
    assert _wait_until(lambda: app.state.active_runs[run_id].done())


def _sample_event(run_id: str, *, node: str) -> dict:
    return {
        "timestamp": "2026-09-12T00:00:00+00:00",
        "run_id": run_id,
        "iteration": 1,
        "phase": "phase3_baseline",
        "node": node,
        "event": "start",
        "duration_ms": None,
        "output_summary": None,
    }


def _parse_data_frames(text: str) -> list[dict]:
    """Extract every `data: {...}` frame from a raw SSE response body, in
    order, ignoring blank lines and `: heartbeat` comment lines."""
    frames = []
    for block in text.split("\n\n"):
        if block.startswith("data:"):
            frames.append(json.loads(block[len("data:") :]))
    return frames


def test_stream_events_unknown_run_returns_404(client: TestClient) -> None:
    response = client.get("/api/runs/unknown-run-id/events")

    assert response.status_code == 404


def test_single_seeded_event_streamed_as_one_data_frame(client: TestClient, app) -> None:
    run_id = "seed-single-event-run"
    registry.create_run_record(app.state.runs_dir, run_id, "titanic", "/ws/titanic")
    event = _sample_event(run_id, node="node_a")
    queue = create_event_queue()
    queue.put_nowait(event)
    queue.put_nowait(STREAM_END)
    app.state.event_queues[run_id] = queue

    response = client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 200
    assert _parse_data_frames(response.text) == [event]


def test_multiple_seeded_events_streamed_in_order(client: TestClient, app) -> None:
    run_id = "seed-ordered-events-run"
    registry.create_run_record(app.state.runs_dir, run_id, "titanic", "/ws/titanic")
    events_in = [_sample_event(run_id, node=f"node_{i}") for i in range(3)]
    queue = create_event_queue()
    for event in events_in:
        queue.put_nowait(event)
    queue.put_nowait(STREAM_END)
    app.state.event_queues[run_id] = queue

    response = client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 200
    assert _parse_data_frames(response.text) == events_in


def test_client_disconnect_mid_stream_stops_generator_without_raising(app) -> None:
    async def scenario() -> None:
        run_id = "disconnect-run"
        registry.create_run_record(app.state.runs_dir, run_id, "titanic", "/ws/titanic")
        first_event = _sample_event(run_id, node="node_a")
        queue = create_event_queue()
        queue.put_nowait(first_event)
        queue.put_nowait(_sample_event(run_id, node="node_b"))
        # Disconnected as of the 2nd `is_disconnected()` check — i.e. right
        # after the first event is yielded, before the second is read.
        request = _FakeRequest(app, disconnected_after=1)

        chunks = [chunk async for chunk in events._event_stream(request, run_id, queue)]

        assert chunks == [f"data: {json.dumps(first_event)}\n\n"]

    asyncio.run(scenario())  # must not raise


def test_full_lifecycle_stream_closes_and_queue_is_removed(
    client: TestClient, app, fake_graph: FakeCompiledGraph
) -> None:
    run_id = _create_run(client)["run_id"]
    _wait_for_run_task_done(app, run_id)

    response = client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 200
    # The fake graph never drives any callback hooks, so the only thing ever
    # queued is the terminal sentinel pushed by `_run_and_track`'s `finally`
    # block — the stream closes with zero `data:` frames.
    assert _parse_data_frames(response.text) == []
    assert run_id not in app.state.event_queues


def test_streaming_after_queue_removed_returns_empty_stream(
    client: TestClient, app, fake_graph: FakeCompiledGraph
) -> None:
    run_id = _create_run(client)["run_id"]
    _wait_for_run_task_done(app, run_id)
    client.get(f"/api/runs/{run_id}/events")  # first stream consumes + removes the queue
    assert run_id not in app.state.event_queues

    response = client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 200
    assert response.text == ""


def test_resume_creates_new_queue_and_attaches_both_callbacks(
    client: TestClient, app, fake_graph: FakeCompiledGraph
) -> None:
    run_id = _create_run(client)["run_id"]
    _wait_for_run_task_done(app, run_id)
    registry.update_run_status(app.state.runs_dir, run_id, "interrupted")
    old_queue = app.state.event_queues[run_id]

    response = client.post(f"/api/runs/{run_id}/resume", json={"feedback": "proceed"})

    assert response.status_code == 200
    new_queue = app.state.event_queues[run_id]
    assert new_queue is not old_queue

    _wait_for_run_task_done(app, run_id)
    callbacks = fake_graph.invoke_calls[-1][1]["callbacks"]
    assert len(callbacks) == 2
    callback_types = {type(callback) for callback in callbacks}
    assert callback_types == {JsonlCallbackHandler, EventEmitter}


def test_heartbeat_comment_sent_while_queue_idle(app, monkeypatch) -> None:
    monkeypatch.setattr(events, "HEARTBEAT_INTERVAL_SECONDS", 0.01)

    async def scenario() -> None:
        run_id = "idle-heartbeat-run"
        registry.create_run_record(app.state.runs_dir, run_id, "titanic", "/ws/titanic")
        queue = create_event_queue()  # never receives an item or STREAM_END
        # Disconnected as of the 2nd check, so exactly one heartbeat interval
        # elapses (yielding one heartbeat) before the generator returns.
        request = _FakeRequest(app, disconnected_after=1)

        chunks = [chunk async for chunk in events._event_stream(request, run_id, queue)]

        assert chunks == [": heartbeat\n\n"]

    asyncio.run(scenario())
