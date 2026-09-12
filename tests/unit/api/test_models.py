"""Unit tests for `src/api/models.py`, in particular the `-inf` -> JSON
`null` serialization behavior that `RunSummary` must provide independently of
any FastAPI wiring."""

import json

from src.api.models import RunSummary


def _make_summary(best_score: float) -> RunSummary:
    return RunSummary(
        run_id="run-1",
        competition_name="titanic",
        workspace_path="/workspace/titanic",
        status="running",
        phase="phase3_baseline",
        current_iteration=1,
        best_score=best_score,
        created_at="2026-09-12T00:00:00+00:00",
        updated_at="2026-09-12T00:00:00+00:00",
    )


def test_negative_infinity_best_score_serializes_as_json_null() -> None:
    summary = _make_summary(float("-inf"))

    raw = summary.model_dump_json()

    assert '"best_score":null' in raw
    assert "Infinity" not in raw
    assert json.loads(raw)["best_score"] is None


def test_finite_best_score_serializes_as_number() -> None:
    summary = _make_summary(0.87)

    raw = summary.model_dump_json()

    assert '"best_score":0.87' in raw
    assert json.loads(raw)["best_score"] == 0.87


def test_best_score_stays_a_real_float_in_python() -> None:
    summary = _make_summary(float("-inf"))

    assert summary.best_score == float("-inf")
