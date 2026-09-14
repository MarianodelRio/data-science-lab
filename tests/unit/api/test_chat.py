"""Unit tests for `WS /api/runs/{id}/chat` (`src/api/routers/chat.py`).

Uses the `client`/`app`/`fake_graph`/`fake_explainer`/`explainer_factory`
fixtures from `conftest.py` — a real `WorkspaceManager` is constructed by the
handler, so every run record here points its `workspace_path` at a `tmp_path`
subdirectory rather than an absolute-looking placeholder like `/ws/titanic`
(fine in `test_runs.py`, which never touches the filesystem via that path,
but would try to `mkdir` at the real filesystem root here).
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from src.api import registry
from src.api.main import create_app
from tests.fixtures.fake_graph import FakeCompiledGraph

_POLL_TIMEOUT_SECONDS = 1.0
_POLL_INTERVAL_SECONDS = 0.01


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


def _seed_run(
    tmp_path: Path,
    run_id: str,
    *,
    status: str = "running",
    competition_name: str = "titanic",
) -> str:
    """Create a run record with a `workspace_path` safely under `tmp_path`,
    and set its status. Returns the workspace path used."""
    workspace_path = str(tmp_path / "workspaces" / run_id)
    registry.create_run_record(tmp_path, run_id, competition_name, workspace_path)
    if status != "pending":
        registry.update_run_status(tmp_path, run_id, status)
    return workspace_path


def test_question_returns_explainer_answer(
    client: TestClient, tmp_path: Path, fake_explainer: MagicMock
) -> None:
    _seed_run(tmp_path, "run-1")

    with client.websocket_connect("/api/runs/run-1/chat") as ws:
        ws.send_json({"type": "question", "text": "what phase?"})
        frame = ws.receive_json()

    assert frame == {"type": "answer", "text": "mocked explainer answer"}
    assert fake_explainer.answer.call_args.args[0] == "what phase?"


def test_malformed_json_returns_error_and_keeps_socket_open(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_run(tmp_path, "run-1")

    with client.websocket_connect("/api/runs/run-1/chat") as ws:
        ws.send_text("not json")
        error_frame = ws.receive_json()
        assert error_frame["type"] == "error"

        ws.send_json({"type": "question", "text": "still there?"})
        answer_frame = ws.receive_json()

    assert answer_frame["type"] == "answer"


def test_missing_type_field_returns_error(client: TestClient, tmp_path: Path) -> None:
    _seed_run(tmp_path, "run-1")

    with client.websocket_connect("/api/runs/run-1/chat") as ws:
        ws.send_json({"text": "hello"})
        frame = ws.receive_json()

    assert frame["type"] == "error"


def test_unknown_type_returns_error(client: TestClient, tmp_path: Path) -> None:
    _seed_run(tmp_path, "run-1")

    with client.websocket_connect("/api/runs/run-1/chat") as ws:
        ws.send_json({"type": "poke"})
        frame = ws.receive_json()

    assert frame["type"] == "error"


def test_question_missing_text_returns_error(client: TestClient, tmp_path: Path) -> None:
    _seed_run(tmp_path, "run-1")

    with client.websocket_connect("/api/runs/run-1/chat") as ws:
        ws.send_json({"type": "question"})
        frame = ws.receive_json()

    assert frame["type"] == "error"


def test_unknown_run_id_sends_error_and_closes(client: TestClient) -> None:
    with client.websocket_connect("/api/runs/does-not-exist/chat") as ws:
        frame = ws.receive_json()
        assert frame["type"] == "error"

        try:
            ws.receive_json()
        except WebSocketDisconnect:
            pass
        else:
            raise AssertionError("expected the socket to be closed by the server")


def test_interrupted_run_sends_checkpoint_frame_on_connect(
    client: TestClient, tmp_path: Path, fake_graph: FakeCompiledGraph
) -> None:
    fake_graph._values.update({"phase": "phase4_design", "checkpoint_summary": "awaiting review"})
    _seed_run(tmp_path, "run-1", status="interrupted")

    with client.websocket_connect("/api/runs/run-1/chat") as ws:
        first_frame = ws.receive_json()

    assert first_frame == {
        "type": "checkpoint",
        "phase": "phase4_design",
        "summary": "awaiting review",
    }


def test_checkpoint_frame_reflects_status_at_send_time_not_at_connect_time(
    tmp_path: Path, fake_graph: FakeCompiledGraph, explainer_factory: MagicMock
) -> None:
    """Regression test: the checkpoint-frame decision must read the run's
    status fresh right before sending it, not the snapshot taken at the top
    of `chat()` before `_build_explainer_session` runs. Session build (graph
    lookup, `WorkspaceManager`, RAG store factory) can take real wall-clock
    time, during which the run can resume via the REST endpoint or another
    chat connection.

    The `rag_store_factory` call is the last thing `_build_explainer_session`
    does, so mutating the on-disk status from inside it deterministically
    simulates a status change that happens *during* session build — no
    sleep-based timing race. With the bug, the handler would still send a
    stale `"checkpoint"` frame here even though the run is no longer
    interrupted by the time the frame would go out.
    """

    def resume_mid_build_rag_store_factory(_competition_name: str) -> None:
        registry.update_run_status(tmp_path, "run-1", "running")
        return None

    app = create_app(
        runs_dir=tmp_path,
        graph_factory=lambda *_a, **_k: fake_graph,
        explainer_factory=explainer_factory,
        rag_store_factory=resume_mid_build_rag_store_factory,
        key_validator=lambda: None,
    )
    with TestClient(app) as client:
        _seed_run(tmp_path, "run-1", status="interrupted")

        with client.websocket_connect("/api/runs/run-1/chat") as ws:
            ws.send_json({"type": "question", "text": "still there?"})
            frame = ws.receive_json()

    assert frame["type"] == "answer"


def test_approve_triggers_resume(
    client: TestClient, tmp_path: Path, fake_graph: FakeCompiledGraph
) -> None:
    _seed_run(tmp_path, "run-1", status="interrupted")

    with client.websocket_connect("/api/runs/run-1/chat") as ws:
        ws.receive_json()  # the automatic checkpoint frame
        ws.send_json({"type": "approve", "feedback": ""})
        frame = ws.receive_json()

    assert frame["type"] == "resumed"
    assert frame["run_id"] == "run-1"
    assert frame["status"] == "running"
    # `update_state` runs inside a background task registered once the
    # "resumed" frame is sent, not necessarily completed by then — poll for
    # it instead of asserting synchronously (same race as
    # `test_runs.py::test_resume_passes_feedback_into_graph`).
    assert _wait_until(lambda: fake_graph.update_state_calls)
    assert fake_graph.update_state_calls[-1][1] == {"human_feedback": ""}


def test_redirect_forwards_feedback_text(
    client: TestClient, tmp_path: Path, fake_graph: FakeCompiledGraph
) -> None:
    _seed_run(tmp_path, "run-1", status="interrupted")
    feedback_text = "try a different validation split"

    with client.websocket_connect("/api/runs/run-1/chat") as ws:
        ws.receive_json()  # the automatic checkpoint frame
        ws.send_json({"type": "redirect", "feedback": feedback_text})
        frame = ws.receive_json()

    assert frame["type"] == "resumed"
    # See the comment in `test_approve_triggers_resume` — same background-task race.
    assert _wait_until(lambda: fake_graph.update_state_calls)
    assert fake_graph.update_state_calls[-1][1] == {"human_feedback": feedback_text}


def test_approve_when_not_interrupted_returns_error_and_keeps_socket_open(
    client: TestClient, tmp_path: Path
) -> None:
    _seed_run(tmp_path, "run-1", status="running")

    with client.websocket_connect("/api/runs/run-1/chat") as ws:
        ws.send_json({"type": "approve", "feedback": ""})
        error_frame = ws.receive_json()
        assert error_frame["type"] == "error"

        ws.send_json({"type": "question", "text": "still there?"})
        answer_frame = ws.receive_json()

    assert answer_frame["type"] == "answer"


def test_approve_when_another_run_is_already_active_returns_error(tmp_path: Path) -> None:
    class SlowFakeGraph(FakeCompiledGraph):
        def invoke(self, input, config):
            time.sleep(0.3)
            return super().invoke(input, config)

    slow_graph = SlowFakeGraph()
    app = create_app(
        runs_dir=tmp_path,
        graph_factory=lambda *_a, **_k: slow_graph,
        explainer_factory=lambda **_kwargs: MagicMock(),
        rag_store_factory=lambda _name: None,
        key_validator=lambda: None,
    )
    with TestClient(app) as client:
        _seed_run(tmp_path, "active-run", status="pending")
        create_response = client.post(
            "/api/runs",
            json={"competition_name": "titanic", "workspace_path": str(tmp_path / "ws-active")},
        )
        active_run_id = create_response.json()["run_id"]
        assert not app.state.active_runs[active_run_id].done()

        _seed_run(tmp_path, "run-1", status="interrupted")

        with client.websocket_connect("/api/runs/run-1/chat") as ws:
            ws.receive_json()  # the automatic checkpoint frame
            ws.send_json({"type": "approve", "feedback": ""})
            frame = ws.receive_json()

        assert frame["type"] == "error"

        deadline = time.monotonic() + 1.0
        while not app.state.active_runs[active_run_id].done() and time.monotonic() < deadline:
            time.sleep(0.01)
