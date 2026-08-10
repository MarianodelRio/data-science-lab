"""Unit tests for src/nodes/compute/baseline_runner.py.

`execute` is mocked at its import location inside
`src.nodes.compute.baseline_runner` (never a real subprocess), `mlflow` is
mocked at the same location (never a real tracking backend), and `Settings`
is mocked there too (avoids requiring real provider API-key env vars just to
read `workspace.mlflow_tracking_uri`). A real `WorkspaceManager` over
`tmp_path`, pre-seeded with `experiments/baseline/design.json` and
`validation/fold_config.json` fixture files, exercises the actual file I/O.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.nodes.compute.baseline_runner import BaselineRunnerNode
from src.state import new_state
from src.tools.code_executor import ExecResult
from src.workspace.workspace_manager import WorkspaceManager

DESIGN = {
    "model": "lightgbm",
    "hyperparameters": {"n_estimators": 100},
    "features": "all",
    "target_column": "target",
    "cv_strategy_ref": "validation/fold_config.json",
}
FOLDS = {
    "strategy": "stratified",
    "n_folds": 2,
    "seed": 42,
    "fold_indices": [
        {"train": [0, 1, 2], "val": [3, 4]},
        {"train": [3, 4], "val": [0, 1, 2]},
    ],
}
STDOUT_PAYLOAD = {"cv_score": 0.87, "fold_scores": [0.85, 0.89], "model": "lightgbm"}


def _seeded_workspace(tmp_path) -> WorkspaceManager:
    workspace = WorkspaceManager(str(tmp_path))
    workspace.write_json("experiments/baseline/design.json", DESIGN)
    workspace.write_json("validation/fold_config.json", FOLDS)
    return workspace


def _build_state(tmp_path) -> dict[str, Any]:
    state = new_state("comp", str(tmp_path))
    return state


@pytest.fixture
def patched_execute():
    with patch("src.nodes.compute.baseline_runner.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0, stdout=json.dumps(STDOUT_PAYLOAD), stderr="", timed_out=False
        )
        yield mock_execute


@pytest.fixture
def patched_mlflow():
    with patch("src.nodes.compute.baseline_runner.mlflow") as mock_mlflow:
        mock_run_ctx = MagicMock()
        mock_mlflow.start_run.return_value.__enter__.return_value = mock_run_ctx
        yield mock_mlflow


@pytest.fixture
def patched_settings():
    with patch("src.nodes.compute.baseline_runner.Settings") as mock_settings_cls:
        settings = MagicMock()
        settings.workspace.mlflow_tracking_uri = "http://mlflow:5000"
        mock_settings_cls.load.return_value = settings
        yield mock_settings_cls


# -- happy path --


def test_run_returns_float_baseline_score(
    patched_execute, patched_mlflow, patched_settings, tmp_path
) -> None:
    _seeded_workspace(tmp_path)
    node = BaselineRunnerNode()
    state = _build_state(tmp_path)

    delta = node.run(state)

    assert delta["baseline_score"] == pytest.approx(0.87)
    assert isinstance(delta["baseline_score"], float)
    assert delta["baseline_results_path"].endswith("experiments/baseline/results.json")


def test_run_writes_results_json_with_cv_score(
    patched_execute, patched_mlflow, patched_settings, tmp_path
) -> None:
    _seeded_workspace(tmp_path)
    node = BaselineRunnerNode()
    state = _build_state(tmp_path)

    node.run(state)

    workspace = WorkspaceManager(str(tmp_path))
    results = workspace.read_json("experiments/baseline/results.json")
    assert results["cv_score"] == pytest.approx(0.87)
    assert results["fold_scores"] == [0.85, 0.89]
    assert results["design"] == DESIGN


def test_run_reads_folds_and_never_writes_them(
    patched_execute, patched_mlflow, patched_settings, tmp_path
) -> None:
    _seeded_workspace(tmp_path)
    folds_path = tmp_path / "validation" / "fold_config.json"
    original_bytes = folds_path.read_bytes()
    node = BaselineRunnerNode()
    state = _build_state(tmp_path)

    node.run(state)

    assert folds_path.read_bytes() == original_bytes


def test_run_executes_code_via_code_executor(
    patched_execute, patched_mlflow, patched_settings, tmp_path
) -> None:
    _seeded_workspace(tmp_path)
    node = BaselineRunnerNode()
    state = _build_state(tmp_path)

    node.run(state)

    patched_execute.assert_called_once()
    args, kwargs = patched_execute.call_args
    assert kwargs["cwd"] == str(tmp_path.resolve())
    assert isinstance(args[0], str)
    assert "fold_config.json" in args[0]


def test_run_logs_to_mlflow(patched_execute, patched_mlflow, patched_settings, tmp_path) -> None:
    _seeded_workspace(tmp_path)
    node = BaselineRunnerNode()
    state = _build_state(tmp_path)

    node.run(state)

    patched_mlflow.set_tracking_uri.assert_called_once_with("http://mlflow:5000")
    patched_mlflow.start_run.assert_called_once_with(run_name="baseline")
    patched_mlflow.log_params.assert_called_once_with(DESIGN["hyperparameters"])
    patched_mlflow.log_metric.assert_called_once_with("cv_score", pytest.approx(0.87))


# -- failure paths --


def test_nonzero_returncode_raises_value_error(patched_mlflow, patched_settings, tmp_path) -> None:
    _seeded_workspace(tmp_path)
    with patch("src.nodes.compute.baseline_runner.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=1, stdout="", stderr="Traceback: boom", timed_out=False
        )
        node = BaselineRunnerNode()
        state = _build_state(tmp_path)

        with pytest.raises(ValueError, match="execution failed"):
            node.run(state)

    workspace = WorkspaceManager(str(tmp_path))
    assert not (tmp_path / "experiments" / "baseline" / "results.json").exists()
    del workspace


def test_timeout_raises_value_error(patched_mlflow, patched_settings, tmp_path) -> None:
    _seeded_workspace(tmp_path)
    with patch("src.nodes.compute.baseline_runner.execute") as mock_execute:
        mock_execute.return_value = ExecResult(returncode=-1, stdout="", stderr="", timed_out=True)
        node = BaselineRunnerNode()
        state = _build_state(tmp_path)

        with pytest.raises(ValueError, match="execution failed"):
            node.run(state)

    assert not (tmp_path / "experiments" / "baseline" / "results.json").exists()


def test_malformed_stdout_raises_value_error(patched_mlflow, patched_settings, tmp_path) -> None:
    _seeded_workspace(tmp_path)
    with patch("src.nodes.compute.baseline_runner.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0, stdout="not json at all", stderr="", timed_out=False
        )
        node = BaselineRunnerNode()
        state = _build_state(tmp_path)

        with pytest.raises(ValueError, match="JSON"):
            node.run(state)

    assert not (tmp_path / "experiments" / "baseline" / "results.json").exists()


def test_empty_stdout_raises_value_error(patched_mlflow, patched_settings, tmp_path) -> None:
    _seeded_workspace(tmp_path)
    with patch("src.nodes.compute.baseline_runner.execute") as mock_execute:
        mock_execute.return_value = ExecResult(returncode=0, stdout="", stderr="", timed_out=False)
        node = BaselineRunnerNode()
        state = _build_state(tmp_path)

        with pytest.raises(ValueError, match="no stdout output"):
            node.run(state)


def test_stdout_missing_cv_score_key_raises_value_error(
    patched_mlflow, patched_settings, tmp_path
) -> None:
    _seeded_workspace(tmp_path)
    with patch("src.nodes.compute.baseline_runner.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0,
            stdout=json.dumps({"fold_scores": [0.1]}),
            stderr="",
            timed_out=False,
        )
        node = BaselineRunnerNode()
        state = _build_state(tmp_path)

        with pytest.raises(ValueError, match="cv_score"):
            node.run(state)


def test_last_line_of_multiline_stdout_is_parsed(
    patched_mlflow, patched_settings, tmp_path
) -> None:
    _seeded_workspace(tmp_path)
    with patch("src.nodes.compute.baseline_runner.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0,
            stdout=f"some noise on stdout\n{json.dumps(STDOUT_PAYLOAD)}\n",
            stderr="",
            timed_out=False,
        )
        node = BaselineRunnerNode()
        state = _build_state(tmp_path)

        delta = node.run(state)

    assert delta["baseline_score"] == pytest.approx(0.87)


def test_call_delegates_to_run(patched_execute, patched_mlflow, patched_settings, tmp_path) -> None:
    _seeded_workspace(tmp_path)
    node = BaselineRunnerNode()
    state = _build_state(tmp_path)

    delta = node(state)

    assert delta["baseline_score"] == pytest.approx(0.87)
