"""Unit tests for `src/api/routers/experiments.py`, via FastAPI `TestClient` +
`FakeCompiledGraph`. Zero network calls, zero LangGraph/sqlite dependency.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import registry
from tests.fixtures.fake_graph import FakeCompiledGraph


def _create_run(app, tmp_path: Path, competition_name: str = "titanic") -> str:
    run_id = uuid.uuid4().hex
    workspace_path = tmp_path / "workspace" / run_id
    registry.create_run_record(app.state.runs_dir, run_id, competition_name, str(workspace_path))
    return run_id


def test_get_experiments_returns_baseline_score_and_best_experiment_path(
    client: TestClient, app, fake_graph: FakeCompiledGraph, tmp_path: Path
) -> None:
    run_id = _create_run(app, tmp_path)
    fake_graph._values.update(
        {
            "experiments": [{"id": "exp_0", "cv_score": 0.72}],
            "baseline_results_path": "reports/baseline.json",
            "baseline_score": 0.5,
            "best_experiment_path": "experiments/exp_0",
        }
    )

    response = client.get(f"/api/runs/{run_id}/experiments")

    assert response.status_code == 200
    assert response.json() == {
        "experiments": [{"id": "exp_0", "cv_score": 0.72}],
        "baseline_score": 0.5,
        "best_experiment_path": "experiments/exp_0",
    }


def test_get_experiments_baseline_score_null_when_baseline_results_path_empty(
    client: TestClient, app, fake_graph: FakeCompiledGraph, tmp_path: Path
) -> None:
    run_id = _create_run(app, tmp_path)
    # `baseline_score` carries the raw `0.0` seed even though no baseline has
    # run yet — `baseline_results_path` empty is the signal it must be
    # reported as null, not the live `0.0` value.
    fake_graph._values.update({"baseline_results_path": "", "baseline_score": 0.0})

    response = client.get(f"/api/runs/{run_id}/experiments")

    assert response.status_code == 200
    assert response.json()["baseline_score"] is None


def test_get_experiments_returns_empty_list_for_fresh_run(
    client: TestClient, app, fake_graph: FakeCompiledGraph, tmp_path: Path
) -> None:
    run_id = _create_run(app, tmp_path)
    fake_graph._values.update({"experiments": []})

    response = client.get(f"/api/runs/{run_id}/experiments")

    assert response.status_code == 200
    assert response.json()["experiments"] == []


def test_get_experiments_pending_run_with_no_checkpoint_returns_defaults(
    client: TestClient, app, tmp_path: Path
) -> None:
    # Default `fake_graph` fixture has `values={}` -> no checkpoint yet.
    run_id = _create_run(app, tmp_path)

    response = client.get(f"/api/runs/{run_id}/experiments")

    assert response.status_code == 200
    assert response.json() == {
        "experiments": [],
        "baseline_score": None,
        "best_experiment_path": "",
    }


def test_get_experiments_passes_through_extra_keys_unchanged(
    client: TestClient, app, fake_graph: FakeCompiledGraph, tmp_path: Path
) -> None:
    run_id = _create_run(app, tmp_path)
    experiment = {"id": "exp_1", "cv_score": 0.8, "unexpected_key": "some value", "n": 3}
    fake_graph._values.update({"experiments": [experiment]})

    response = client.get(f"/api/runs/{run_id}/experiments")

    assert response.status_code == 200
    assert response.json()["experiments"] == [experiment]


def test_get_experiments_sanitizes_inf_and_nan_cv_score(
    client: TestClient, app, fake_graph: FakeCompiledGraph, tmp_path: Path
) -> None:
    run_id = _create_run(app, tmp_path)
    fake_graph._values.update(
        {
            "experiments": [
                {"id": "exp_bad_1", "cv_score": float("-inf")},
                {"id": "exp_bad_2", "cv_score": float("nan")},
            ]
        }
    )

    response = client.get(f"/api/runs/{run_id}/experiments")

    assert response.status_code == 200
    # Valid JSON parse proves no `-Infinity`/`NaN` token made it into the body.
    body = response.json()
    experiments = body["experiments"]
    assert experiments[0]["cv_score"] is None
    assert experiments[1]["cv_score"] is None
    assert "Infinity" not in response.text
    assert "NaN" not in response.text


def test_get_experiments_returns_404_for_unknown_run_id(client: TestClient) -> None:
    response = client.get("/api/runs/unknown-run-id/experiments")

    assert response.status_code == 404


def test_get_experiments_returns_404_for_malformed_run_id(client: TestClient) -> None:
    response = client.get("/api/runs/abc..def/experiments")

    assert response.status_code == 404
    assert "detail" in response.json()
