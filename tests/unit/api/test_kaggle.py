"""Unit tests for `src/api/routers/kaggle.py`, via FastAPI `TestClient` +
`FakeCompiledGraph`. `kaggle_client.submit`/`get_score` are monkeypatched on
the `kaggle` router module — zero network calls, zero real Kaggle credentials
needed. Run records use a real `tmp_path`-backed `workspace_path` (unlike
`test_runs.py`'s untouched placeholder paths) so `submission.csv` presence
checks hit a real filesystem.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import registry
from src.api.routers import kaggle as kaggle_router
from src.workspace.workspace_manager import WorkspaceManager
from tests.fixtures.fake_graph import FakeCompiledGraph

_NO_BEST_EXPERIMENT_DETAIL = "run has no best experiment yet"


def _create_run(app, tmp_path: Path, competition_name: str = "titanic") -> tuple[str, Path]:
    """Create a registry record with a real, tmp_path-backed workspace_path
    (not yet materialized on disk — `WorkspaceManager` creates it lazily)."""
    run_id = uuid.uuid4().hex
    workspace_path = tmp_path / "workspace" / run_id
    registry.create_run_record(app.state.runs_dir, run_id, competition_name, str(workspace_path))
    return run_id, workspace_path


def _write_submission(workspace_path: Path, experiment_name: str) -> Path:
    """Write a real submission.csv under the workspace's resolved experiment
    directory, returning the resolved path."""
    experiment_dir = workspace_path / "experiments" / experiment_name
    experiment_dir.mkdir(parents=True, exist_ok=True)
    submission_path = experiment_dir / "submission.csv"
    submission_path.write_text("id,target\n1,0.5\n", encoding="utf-8")
    return submission_path


def test_submit_returns_404_for_unknown_run(client: TestClient) -> None:
    response = client.post("/api/runs/unknown-run-id/submit")

    assert response.status_code == 404


def test_submit_returns_409_when_best_experiment_path_empty(
    client: TestClient, app, tmp_path: Path
) -> None:
    # Default `fake_graph` fixture has `values={}` -> no `best_experiment_path`.
    run_id, _ = _create_run(app, tmp_path)

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 409
    assert response.json()["detail"] == _NO_BEST_EXPERIMENT_DETAIL


def test_submit_returns_409_when_submission_csv_missing(
    client: TestClient, app, fake_graph: FakeCompiledGraph, tmp_path: Path
) -> None:
    run_id, workspace_path = _create_run(app, tmp_path)
    fake_graph._values.update(
        {"best_experiment_path": str(workspace_path / "experiments" / "exp_3")}
    )

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "experiments/exp_3/submission.csv" in detail


def test_submit_returns_409_for_malformed_experiment_pointer(
    client: TestClient, app, fake_graph: FakeCompiledGraph, tmp_path: Path
) -> None:
    run_id, workspace_path = _create_run(app, tmp_path)
    # basename ".." makes `WorkspaceManager.experiment_dir` raise `ValueError`
    # (path traversal guard) rather than resolving to a real directory.
    fake_graph._values.update({"best_experiment_path": str(workspace_path / "experiments" / "..")})

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 409
    assert response.json()["detail"] == _NO_BEST_EXPERIMENT_DETAIL


def test_submit_returns_409_when_experiment_name_is_empty(
    client: TestClient, app, fake_graph: FakeCompiledGraph, tmp_path: Path
) -> None:
    # `Path("/").name == ""` — a `best_experiment_path` that resolves to just
    # the filesystem root has no final component, so `experiment_name` would
    # be empty. Must not build a malformed "experiments//submission.csv"
    # message; treated the same as an unset `best_experiment_path`.
    run_id, _ = _create_run(app, tmp_path)
    fake_graph._values.update({"best_experiment_path": "/"})

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 409
    assert response.json()["detail"] == _NO_BEST_EXPERIMENT_DETAIL


def test_submit_succeeds_and_returns_public_score(
    client: TestClient,
    app,
    fake_graph: FakeCompiledGraph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, workspace_path = _create_run(app, tmp_path, competition_name="titanic")
    submission_path = _write_submission(workspace_path, "exp_2")
    # `best_experiment_path` carries a prefix unrelated to `workspace_path` —
    # only its basename ("exp_2") is used to resolve the real directory via
    # `WorkspaceManager.experiment_dir`.
    fake_graph._values.update({"best_experiment_path": "/some/other/root/experiments/exp_2"})

    calls: dict[str, tuple] = {}

    def fake_submit(competition: str, file_path: str, message: str) -> None:
        calls["submit"] = (competition, file_path, message)

    def fake_get_score(competition: str) -> dict:
        calls["get_score"] = (competition,)
        return {"public_score": 0.85, "submitted_at": "2026-01-01T00:00:00+00:00"}

    monkeypatch.setattr(kaggle_router.kaggle_client, "submit", fake_submit)
    monkeypatch.setattr(kaggle_router.kaggle_client, "get_score", fake_get_score)

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 200
    assert response.json() == {
        "public_score": 0.85,
        "submission_file": "experiments/exp_2/submission.csv",
        "message": None,
    }
    assert calls["submit"][0] == "titanic"
    assert calls["submit"][1] == str(submission_path)
    assert isinstance(calls["submit"][2], str)
    assert calls["get_score"] == ("titanic",)


def test_submit_returns_409_for_invalid_competition_slug(
    client: TestClient, app, fake_graph: FakeCompiledGraph, tmp_path: Path
) -> None:
    run_id, workspace_path = _create_run(app, tmp_path, competition_name="not a valid slug!")
    _write_submission(workspace_path, "exp_1")
    fake_graph._values.update(
        {"best_experiment_path": str(workspace_path / "experiments" / "exp_1")}
    )

    # No mocking of `submit` here: the real `_validate_competition` inside
    # `kaggle_client.submit` fails before any network/`_default_api()` call.
    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 409
    assert "not a valid slug!" in response.json()["detail"]


def test_submit_returns_503_when_kaggle_credentials_missing(
    client: TestClient,
    app,
    fake_graph: FakeCompiledGraph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, workspace_path = _create_run(app, tmp_path)
    _write_submission(workspace_path, "exp_1")
    fake_graph._values.update(
        {"best_experiment_path": str(workspace_path / "experiments" / "exp_1")}
    )

    def fake_submit(competition: str, file_path: str, message: str) -> None:
        raise RuntimeError("Missing required environment variable 'KAGGLE_USERNAME'")

    monkeypatch.setattr(kaggle_router.kaggle_client, "submit", fake_submit)

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 503


def test_submit_returns_502_on_other_kaggle_submit_failure(
    client: TestClient,
    app,
    fake_graph: FakeCompiledGraph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, workspace_path = _create_run(app, tmp_path)
    _write_submission(workspace_path, "exp_1")
    fake_graph._values.update(
        {"best_experiment_path": str(workspace_path / "experiments" / "exp_1")}
    )

    def fake_submit(competition: str, file_path: str, message: str) -> None:
        raise Exception("HTTP 500")

    monkeypatch.setattr(kaggle_router.kaggle_client, "submit", fake_submit)

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 502


def test_submit_returns_200_with_null_public_score_when_get_score_raises_type_error(
    client: TestClient,
    app,
    fake_graph: FakeCompiledGraph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, workspace_path = _create_run(app, tmp_path)
    _write_submission(workspace_path, "exp_1")
    fake_graph._values.update(
        {"best_experiment_path": str(workspace_path / "experiments" / "exp_1")}
    )

    monkeypatch.setattr(
        kaggle_router.kaggle_client, "submit", lambda competition, file_path, message: None
    )

    def fake_get_score(competition: str) -> dict:
        raise TypeError("'NoneType' object is not subscriptable")

    monkeypatch.setattr(kaggle_router.kaggle_client, "get_score", fake_get_score)

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 200
    body = response.json()
    assert body["public_score"] is None
    assert body["message"] is not None
    assert "not" in body["message"] and "scored" in body["message"]


def test_submit_returns_502_when_get_score_raises_other_exception(
    client: TestClient,
    app,
    fake_graph: FakeCompiledGraph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id, workspace_path = _create_run(app, tmp_path)
    _write_submission(workspace_path, "exp_1")
    fake_graph._values.update(
        {"best_experiment_path": str(workspace_path / "experiments" / "exp_1")}
    )

    monkeypatch.setattr(
        kaggle_router.kaggle_client, "submit", lambda competition, file_path, message: None
    )

    def fake_get_score(competition: str) -> dict:
        raise RuntimeError("No submissions found for competition 'titanic'")

    monkeypatch.setattr(kaggle_router.kaggle_client, "get_score", fake_get_score)

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 502


def test_submit_uses_asyncio_to_thread_and_does_not_block_event_loop(
    client: TestClient,
    app,
    fake_graph: FakeCompiledGraph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _SUBMIT_SLEEP_SECONDS = 0.3

    run_id, workspace_path = _create_run(app, tmp_path)
    _write_submission(workspace_path, "exp_1")
    fake_graph._values.update(
        {"best_experiment_path": str(workspace_path / "experiments" / "exp_1")}
    )

    def slow_submit(competition: str, file_path: str, message: str) -> None:
        time.sleep(_SUBMIT_SLEEP_SECONDS)

    monkeypatch.setattr(kaggle_router.kaggle_client, "submit", slow_submit)
    monkeypatch.setattr(
        kaggle_router.kaggle_client,
        "get_score",
        lambda competition: {"public_score": 0.5, "submitted_at": "2026-01-01T00:00:00+00:00"},
    )

    results: dict[str, object] = {}

    def do_submit() -> None:
        start = time.monotonic()
        response = client.post(f"/api/runs/{run_id}/submit")
        results["status_code"] = response.status_code
        results["duration"] = time.monotonic() - start

    submit_thread = threading.Thread(target=do_submit)
    submit_thread.start()
    time.sleep(0.05)  # let the submit request enter its blocking sleep

    concurrent_start = time.monotonic()
    concurrent_response = client.get("/api/runs")
    concurrent_duration = time.monotonic() - concurrent_start

    submit_thread.join(timeout=2.0)

    assert concurrent_response.status_code == 200
    # Proves the event loop stayed free while `submit` was sleeping in its
    # worker thread: a concurrent request completes well under the sleep.
    assert concurrent_duration < _SUBMIT_SLEEP_SECONDS / 2
    assert results["status_code"] == 200
    assert results["duration"] >= _SUBMIT_SLEEP_SECONDS


def test_submit_returns_409_when_submission_already_in_progress(
    client: TestClient,
    app,
    fake_graph: FakeCompiledGraph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADV-7ba87f78: a second request for a `run_id` already mid-submission
    must be rejected with 409 before it can fire a real Kaggle call, not just
    eventually agree on the same result."""
    run_id, workspace_path = _create_run(app, tmp_path)
    _write_submission(workspace_path, "exp_1")
    fake_graph._values.update(
        {"best_experiment_path": str(workspace_path / "experiments" / "exp_1")}
    )
    submit_calls: list[str] = []
    monkeypatch.setattr(
        kaggle_router.kaggle_client,
        "submit",
        lambda competition, file_path, message: submit_calls.append(competition),
    )
    # Simulate a submission already in flight for this run_id (as if a first
    # request registered it and is still awaiting Kaggle).
    app.state.active_submissions.add(run_id)

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 409
    assert response.json()["detail"] == "a submission for this run is already in progress"
    assert submit_calls == []


def test_submit_clears_in_flight_guard_on_success(
    client: TestClient,
    app,
    fake_graph: FakeCompiledGraph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard must be released on the success path so a later, unrelated
    submit for the same run_id is never permanently locked out."""
    run_id, workspace_path = _create_run(app, tmp_path)
    _write_submission(workspace_path, "exp_1")
    fake_graph._values.update(
        {"best_experiment_path": str(workspace_path / "experiments" / "exp_1")}
    )
    monkeypatch.setattr(
        kaggle_router.kaggle_client, "submit", lambda competition, file_path, message: None
    )
    monkeypatch.setattr(
        kaggle_router.kaggle_client,
        "get_score",
        lambda competition: {"public_score": 0.5, "submitted_at": "2026-01-01T00:00:00+00:00"},
    )

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 200
    assert run_id not in app.state.active_submissions


def test_submit_clears_in_flight_guard_on_error_exit(
    client: TestClient, app, tmp_path: Path
) -> None:
    """The guard must also clear on every failure exit (the `finally` in
    `submit_run`), not only the success path — checked here via a 409 that
    fires before any Kaggle call."""
    run_id, _ = _create_run(app, tmp_path)

    response = client.post(f"/api/runs/{run_id}/submit")

    assert response.status_code == 409
    assert run_id not in app.state.active_submissions


def test_submit_resolves_submission_path_off_event_loop(
    client: TestClient,
    app,
    fake_graph: FakeCompiledGraph,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADV-2e66cc85: extends
    `test_submit_uses_asyncio_to_thread_and_does_not_block_event_loop` to
    cover the `WorkspaceManager`/`experiment_dir`/`is_file` resolution step
    specifically — a slow `WorkspaceManager` construction must not block the
    event loop either, proving that step also runs via `asyncio.to_thread`."""
    _RESOLVE_SLEEP_SECONDS = 0.3

    run_id, workspace_path = _create_run(app, tmp_path)
    _write_submission(workspace_path, "exp_1")
    fake_graph._values.update(
        {"best_experiment_path": str(workspace_path / "experiments" / "exp_1")}
    )
    monkeypatch.setattr(
        kaggle_router.kaggle_client, "submit", lambda competition, file_path, message: None
    )
    monkeypatch.setattr(
        kaggle_router.kaggle_client,
        "get_score",
        lambda competition: {"public_score": 0.5, "submitted_at": "2026-01-01T00:00:00+00:00"},
    )

    class _SlowWorkspaceManager(WorkspaceManager):
        def __init__(self, workspace_path: str) -> None:
            time.sleep(_RESOLVE_SLEEP_SECONDS)
            super().__init__(workspace_path)

    monkeypatch.setattr(kaggle_router, "WorkspaceManager", _SlowWorkspaceManager)

    results: dict[str, object] = {}

    def do_submit() -> None:
        start = time.monotonic()
        response = client.post(f"/api/runs/{run_id}/submit")
        results["status_code"] = response.status_code
        results["duration"] = time.monotonic() - start

    submit_thread = threading.Thread(target=do_submit)
    submit_thread.start()
    time.sleep(0.05)  # let the submit request enter the slow constructor

    concurrent_start = time.monotonic()
    concurrent_response = client.get("/api/runs")
    concurrent_duration = time.monotonic() - concurrent_start

    submit_thread.join(timeout=2.0)

    assert concurrent_response.status_code == 200
    # Proves the event loop stayed free while `WorkspaceManager.__init__` was
    # sleeping in its worker thread: a concurrent request completes well
    # under the sleep.
    assert concurrent_duration < _RESOLVE_SLEEP_SECONDS / 2
    assert results["status_code"] == 200
    assert results["duration"] >= _RESOLVE_SLEEP_SECONDS
