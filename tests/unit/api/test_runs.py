"""Unit tests for `src/api/routers/runs.py`, via FastAPI `TestClient` +
`FakeCompiledGraph`. Zero network calls, zero LangGraph/sqlite dependency.
"""

import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import registry
from src.api.event_emitter import EventEmitter
from src.api.main import create_app
from src.observability.jsonl_callback import JsonlCallbackHandler
from tests.fixtures.fake_graph import FakeCompiledGraph

_POLL_TIMEOUT_SECONDS = 1.0
_POLL_INTERVAL_SECONDS = 0.01
_RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _wait_until(predicate, timeout: float = _POLL_TIMEOUT_SECONDS) -> bool:
    """Poll `predicate` until it is truthy or `timeout` elapses. Returns
    whether it became truthy — callers assert on that so a timeout fails
    with a clear message instead of hanging."""
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


def test_create_run_returns_201_and_starts_background_task(
    client: TestClient, app, fake_graph: FakeCompiledGraph, tmp_path: Path
) -> None:
    body = _create_run(client)

    assert isinstance(body["run_id"], str)
    assert body["status"] == "pending"

    _wait_for_run_task_done(app, body["run_id"])

    assert len(fake_graph.invoke_calls) == 1
    assert (tmp_path / body["run_id"] / "run.json").exists()


def test_get_run_returns_phase_and_current_iteration(
    client: TestClient, app, fake_graph: FakeCompiledGraph
) -> None:
    fake_graph._values.update({"phase": "phase3_baseline", "current_iteration": 2})
    run_id = _create_run(client)["run_id"]
    _wait_for_run_task_done(app, run_id)

    response = client.get(f"/api/runs/{run_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["phase"] == "phase3_baseline"
    assert data["current_iteration"] == 2


def test_resume_passes_feedback_into_graph(
    client: TestClient, app, fake_graph: FakeCompiledGraph
) -> None:
    run_id = _create_run(client)["run_id"]
    _wait_for_run_task_done(app, run_id)
    registry.update_run_status(app.state.runs_dir, run_id, "interrupted")

    response = client.post(f"/api/runs/{run_id}/resume", json={"feedback": "proceed"})

    assert response.status_code == 200
    # `update_state` now runs inside the background task (see
    # `_resume_and_track`), so it is no longer guaranteed to have happened
    # by the time the HTTP response comes back — poll for it instead of
    # asserting synchronously.
    assert _wait_until(lambda: fake_graph.update_state_calls)
    assert fake_graph.update_state_calls[-1] == (
        {"configurable": {"thread_id": run_id}},
        {"human_feedback": "proceed"},
    )
    _wait_for_run_task_done(app, run_id)


def test_get_unknown_run_returns_404(client: TestClient) -> None:
    response = client.get("/api/runs/unknown-run-id")

    assert response.status_code == 404


def test_resume_unknown_run_returns_404(client: TestClient) -> None:
    response = client.post("/api/runs/unknown-run-id/resume", json={"feedback": "proceed"})

    assert response.status_code == 404


def test_resume_running_run_returns_409(client: TestClient, app) -> None:
    run_id = _create_run(client)["run_id"]
    _wait_for_run_task_done(app, run_id)
    registry.update_run_status(app.state.runs_dir, run_id, "running")

    response = client.post(f"/api/runs/{run_id}/resume", json={"feedback": "proceed"})

    assert response.status_code == 409


def test_resume_completed_run_returns_409(client: TestClient, app) -> None:
    run_id = _create_run(client)["run_id"]
    _wait_for_run_task_done(app, run_id)  # fake graph's empty `next` -> "completed"

    response = client.post(f"/api/runs/{run_id}/resume", json={"feedback": "proceed"})

    assert response.status_code == 409


def test_create_run_conflicts_with_active_run_returns_409(tmp_path: Path) -> None:
    class SlowFakeGraph(FakeCompiledGraph):
        def invoke(self, input, config):
            time.sleep(0.3)
            return super().invoke(input, config)

    slow_graph = SlowFakeGraph()
    app = create_app(runs_dir=tmp_path, graph_factory=lambda *_a, **_k: slow_graph)
    with TestClient(app) as client:
        first = _create_run(client)
        assert not app.state.active_runs[first["run_id"]].done()

        response = client.post(
            "/api/runs",
            json={"competition_name": "spaceship", "workspace_path": "/workspace/spaceship"},
        )

        assert response.status_code == 409
        _wait_for_run_task_done(app, first["run_id"])  # let the background task finish cleanly


def test_resume_conflicts_with_active_run_returns_409(tmp_path: Path) -> None:
    """Regression test for ADV-09702150: `resume_run` had no active-run gate
    at all (unlike `create_run`), so resuming one (different) interrupted run
    could start it executing concurrently with an already-active run."""

    class SlowFakeGraph(FakeCompiledGraph):
        def invoke(self, input, config):
            time.sleep(0.3)
            return super().invoke(input, config)

    slow_graph = SlowFakeGraph()
    app = create_app(runs_dir=tmp_path, graph_factory=lambda *_a, **_k: slow_graph)
    with TestClient(app) as client:
        active = _create_run(client)
        assert not app.state.active_runs[active["run_id"]].done()

        interrupted_run_id = "other-interrupted-run"
        registry.create_run_record(tmp_path, interrupted_run_id, "spaceship", "/ws/spaceship")
        registry.update_run_status(tmp_path, interrupted_run_id, "interrupted")

        response = client.post(
            f"/api/runs/{interrupted_run_id}/resume", json={"feedback": "proceed"}
        )

        assert response.status_code == 409
        assert interrupted_run_id not in app.state.active_runs
        _wait_for_run_task_done(app, active["run_id"])  # let the background task finish cleanly


def test_resume_same_run_twice_only_schedules_one_execution(tmp_path: Path) -> None:
    """Regression test for ADV-09702150's same-run TOCTOU race: the previous
    implementation awaited `graph.update_state` *before* registering the
    background task in `active_runs`, leaving a window where a second
    `resume` call for the same run_id could pass the interrupted-status check
    before the first call's task was registered, scheduling two concurrent
    executions against the same checkpoint (and orphaning the first one in
    `active_runs`).

    The fix removes every `await` between the interrupted-status check and
    the `active_runs[run_id] = task` assignment in `resume_run` (the whole
    `update_state` + execution flow now happens inside the scheduled task,
    see `_resume_and_track`), so nothing can interleave in that window: the
    first call to actually reach the checks registers its task, and the
    second call is turned away with 409 before it ever reaches
    `update_state` — either by the new `_has_active_run` gate (if the first
    task is still running) or by the interrupted-status check (if the first
    task has already finished and advanced the recorded status). Either way,
    only one `update_state`/`invoke` pair ever happens.
    """
    run_id = "double-resume-run"
    registry.create_run_record(tmp_path, run_id, "titanic", "/ws/titanic")
    registry.update_run_status(tmp_path, run_id, "interrupted")

    fake_graph = FakeCompiledGraph()
    app = create_app(runs_dir=tmp_path, graph_factory=lambda *_a, **_k: fake_graph)
    with TestClient(app) as client:
        first = client.post(f"/api/runs/{run_id}/resume", json={"feedback": "first"})
        second = client.post(f"/api/runs/{run_id}/resume", json={"feedback": "second"})

        statuses = sorted([first.status_code, second.status_code])
        assert statuses == [200, 409]

        _wait_for_run_task_done(app, run_id)
        # Exactly one `update_state` call ever reached the graph, from the
        # one request that won the gate — never two concurrent executions
        # against the same checkpoint.
        assert len(fake_graph.update_state_calls) == 1
        assert len(fake_graph.invoke_calls) == 1


def test_list_runs_returns_all_runs_with_status(client: TestClient, app) -> None:
    first = _create_run(client, competition_name="titanic", workspace_path="/ws/titanic")
    _wait_for_run_task_done(app, first["run_id"])
    second = _create_run(client, competition_name="spaceship", workspace_path="/ws/spaceship")
    _wait_for_run_task_done(app, second["run_id"])

    response = client.get("/api/runs")

    assert response.status_code == 200
    runs_by_id = {run["run_id"]: run for run in response.json()}
    assert set(runs_by_id) == {first["run_id"], second["run_id"]}
    assert runs_by_id[first["run_id"]]["status"] == "completed"
    assert runs_by_id[second["run_id"]]["status"] == "completed"


def test_best_score_negative_infinity_serializes_as_json_null(
    client: TestClient, app, fake_graph: FakeCompiledGraph
) -> None:
    fake_graph._values.update({"best_score": float("-inf")})
    run_id = _create_run(client)["run_id"]
    _wait_for_run_task_done(app, run_id)

    response = client.get(f"/api/runs/{run_id}")

    assert response.json()["best_score"] is None
    assert '"best_score":null' in response.text
    assert "Infinity" not in response.text


def test_jsonl_callback_handler_attached_to_graph_invoke_config(
    client: TestClient, app, fake_graph: FakeCompiledGraph
) -> None:
    run_id = _create_run(client)["run_id"]
    _wait_for_run_task_done(app, run_id)

    assert len(fake_graph.invoke_calls) == 1
    callbacks = fake_graph.invoke_calls[0][1]["callbacks"]
    assert len(callbacks) == 2
    jsonl_callbacks = [c for c in callbacks if isinstance(c, JsonlCallbackHandler)]
    assert len(jsonl_callbacks) == 1
    assert jsonl_callbacks[0].run_id == run_id


def test_create_run_attaches_one_jsonl_callback_and_one_event_emitter(
    client: TestClient, app, fake_graph: FakeCompiledGraph
) -> None:
    run_id = _create_run(client)["run_id"]
    _wait_for_run_task_done(app, run_id)

    callbacks = fake_graph.invoke_calls[0][1]["callbacks"]
    assert len(callbacks) == 2
    callback_types = {type(callback) for callback in callbacks}
    assert callback_types == {JsonlCallbackHandler, EventEmitter}


def test_create_run_registers_event_queue_synchronously_before_background_task(
    client: TestClient, app
) -> None:
    """`app.state.event_queues[run_id]` must exist as soon as `POST
    /api/runs` returns — before the background task has necessarily started
    or finished — so a client can `GET .../events` immediately."""
    body = _create_run(client)

    assert body["run_id"] in app.state.event_queues


def test_run_id_is_server_generated_uuid_hex(client: TestClient) -> None:
    body = _create_run(client)

    assert _RUN_ID_PATTERN.match(body["run_id"])


def test_invalid_run_id_treated_as_not_found(client: TestClient) -> None:
    response = client.get("/api/runs/abc..def")

    assert response.status_code == 404
    assert "detail" in response.json()


def test_create_run_rejects_empty_competition_name_and_workspace_path(client: TestClient) -> None:
    empty_name = client.post(
        "/api/runs", json={"competition_name": "", "workspace_path": "/workspace/titanic"}
    )
    empty_path = client.post(
        "/api/runs", json={"competition_name": "titanic", "workspace_path": ""}
    )

    assert empty_name.status_code == 422
    assert empty_path.status_code == 422


def test_get_run_builds_graph_at_most_once_across_repeated_requests(tmp_path: Path) -> None:
    """Regression test for SEC-99f25ece / CQ-6e49f265: `graph_factory` must
    be called at most once per `run_id` per process lifetime, not once per
    request — the real factory opens an unclosed sqlite connection every
    time it runs."""
    build_calls: list[str] = []

    def counting_factory(run_id: str, runs_dir: Path) -> FakeCompiledGraph:
        build_calls.append(run_id)
        return FakeCompiledGraph()

    app = create_app(runs_dir=tmp_path, graph_factory=counting_factory)
    with TestClient(app) as client:
        run_id = _create_run(client)["run_id"]
        _wait_for_run_task_done(app, run_id)
        assert build_calls == [run_id]  # create_run built (and cached) the graph once

        first = client.get(f"/api/runs/{run_id}")
        second = client.get(f"/api/runs/{run_id}")

        assert first.status_code == 200
        assert second.status_code == 200
        assert build_calls == [run_id]  # both GETs hit the cache — no rebuild


def test_list_runs_builds_each_graph_at_most_once_across_repeated_calls(tmp_path: Path) -> None:
    build_calls: list[str] = []

    def counting_factory(run_id: str, runs_dir: Path) -> FakeCompiledGraph:
        build_calls.append(run_id)
        return FakeCompiledGraph()

    app = create_app(runs_dir=tmp_path, graph_factory=counting_factory)
    with TestClient(app) as client:
        run_id = _create_run(client)["run_id"]
        _wait_for_run_task_done(app, run_id)
        build_calls.clear()  # only care about GET /api/runs behavior below

        client.get("/api/runs")
        client.get("/api/runs")

        assert build_calls == []  # create_run already cached this run's graph


def test_create_run_rejects_non_positive_max_iterations(client: TestClient) -> None:
    response = client.post(
        "/api/runs",
        json={
            "competition_name": "titanic",
            "workspace_path": "/workspace/titanic",
            "max_iterations": 0,
        },
    )

    assert response.status_code == 422
