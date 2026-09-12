"""Unit tests for `src/api/routers/runs.py`, via FastAPI `TestClient` +
`FakeCompiledGraph`. Zero network calls, zero LangGraph/sqlite dependency.
"""

import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import registry
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
    _wait_for_run_task_done(app, run_id)
    assert fake_graph.update_state_calls[-1] == (
        {"configurable": {"thread_id": run_id}},
        {"human_feedback": "proceed"},
    )


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
    assert len(callbacks) == 1
    assert isinstance(callbacks[0], JsonlCallbackHandler)
    assert callbacks[0].run_id == run_id


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
