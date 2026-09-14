"""Unit tests for `src/api/routers/files.py`, via FastAPI `TestClient`. Zero
network calls, zero LangGraph dependency — this router never touches the
compiled graph. Run records use a real `tmp_path`-backed `workspace_path`
(same convention as `test_kaggle.py`) so path resolution hits a real
filesystem.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import registry
from src.api.routers import files as files_router
from src.workspace.workspace_manager import WorkspaceManager


def _create_run(app, tmp_path: Path, competition_name: str = "titanic") -> tuple[str, Path]:
    run_id = uuid.uuid4().hex
    workspace_path = tmp_path / "workspace" / run_id
    registry.create_run_record(app.state.runs_dir, run_id, competition_name, str(workspace_path))
    return run_id, workspace_path


def test_get_file_returns_real_file_content(client: TestClient, app, tmp_path: Path) -> None:
    run_id, workspace_path = _create_run(app, tmp_path)
    WorkspaceManager(str(workspace_path)).write_text("notes.md", "# Notes\nhello workspace\n")

    response = client.get(f"/api/runs/{run_id}/files/notes.md")

    assert response.status_code == 200
    assert response.text == "# Notes\nhello workspace\n"
    assert response.headers["content-type"].startswith("text/plain")


def test_get_file_returns_404_for_unknown_run(client: TestClient) -> None:
    response = client.get("/api/runs/unknown-run-id/files/notes.md")

    assert response.status_code == 404


def test_get_file_returns_404_when_file_not_found(client: TestClient, app, tmp_path: Path) -> None:
    run_id, _ = _create_run(app, tmp_path)

    response = client.get(f"/api/runs/{run_id}/files/does_not_exist.txt")

    assert response.status_code == 404


def test_get_file_returns_404_when_path_is_a_directory(
    client: TestClient, app, tmp_path: Path
) -> None:
    run_id, workspace_path = _create_run(app, tmp_path)
    WorkspaceManager(str(workspace_path)).ensure_dir("experiments/exp_1")

    response = client.get(f"/api/runs/{run_id}/files/experiments/exp_1")

    assert response.status_code == 404


def test_get_file_rejects_path_traversal(client: TestClient, app, tmp_path: Path) -> None:
    run_id, _ = _create_run(app, tmp_path)

    # Percent-encoded so httpx doesn't normalize ".." away before sending.
    response = client.get(f"/api/runs/{run_id}/files/a/%2e%2e/secret")

    assert response.status_code == 400


def test_get_file_rejects_absolute_path(client: TestClient, app, tmp_path: Path) -> None:
    run_id, _ = _create_run(app, tmp_path)

    # Double slash keeps the leading "/" inside the `path` param.
    response = client.get(f"/api/runs/{run_id}/files//etc/passwd")

    assert response.status_code == 400


def test_get_file_rejects_empty_path_segment(client: TestClient, app, tmp_path: Path) -> None:
    run_id, _ = _create_run(app, tmp_path)

    response = client.get(f"/api/runs/{run_id}/files/")

    assert response.status_code == 400


def test_get_file_returns_415_for_non_utf8_content(client: TestClient, app, tmp_path: Path) -> None:
    run_id, workspace_path = _create_run(app, tmp_path)
    binary_dir = workspace_path
    binary_dir.mkdir(parents=True, exist_ok=True)
    (binary_dir / "model.bin").write_bytes(b"\xff\xfe\x00\x01binary-not-utf8")

    response = client.get(f"/api/runs/{run_id}/files/model.bin")

    assert response.status_code == 415


def test_get_file_resolves_and_reads_off_event_loop(
    client: TestClient,
    app,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors `test_kaggle.py::test_submit_resolves_submission_path_off_event_loop`'s
    pattern: a slow `WorkspaceManager` construction must not block the event
    loop, proving resolve+read runs via `asyncio.to_thread`."""
    _RESOLVE_SLEEP_SECONDS = 0.3

    run_id, workspace_path = _create_run(app, tmp_path)
    WorkspaceManager(str(workspace_path)).write_text("notes.md", "hello")

    class _SlowWorkspaceManager(WorkspaceManager):
        def __init__(self, workspace_path: str) -> None:
            time.sleep(_RESOLVE_SLEEP_SECONDS)
            super().__init__(workspace_path)

    monkeypatch.setattr(files_router, "WorkspaceManager", _SlowWorkspaceManager)

    results: dict[str, object] = {}

    def do_get() -> None:
        start = time.monotonic()
        response = client.get(f"/api/runs/{run_id}/files/notes.md")
        results["status_code"] = response.status_code
        results["duration"] = time.monotonic() - start

    get_thread = threading.Thread(target=do_get)
    get_thread.start()
    time.sleep(0.05)  # let the request enter the slow constructor

    concurrent_start = time.monotonic()
    concurrent_response = client.get("/api/runs")
    concurrent_duration = time.monotonic() - concurrent_start

    get_thread.join(timeout=2.0)

    assert concurrent_response.status_code == 200
    # Proves the event loop stayed free while `WorkspaceManager.__init__` was
    # sleeping in its worker thread: a concurrent request completes well
    # under the sleep.
    assert concurrent_duration < _RESOLVE_SLEEP_SECONDS / 2
    assert results["status_code"] == 200
    assert results["duration"] >= _RESOLVE_SLEEP_SECONDS
