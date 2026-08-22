"""Unit tests for src/nodes/llm/coder.py.

`coder` overrides `LLMNode.__call__` wholesale and instantiates a *real*
`WorkspaceManager` against `tmp_path` (per the Planner's plan — this node's
control flow does real file existence checks via `Path.exists()`, which a
mocked `WorkspaceManager` cannot satisfy transparently). `LLMFactory` is
mocked at `src.nodes.llm.base` (the inherited `LLMNode.__init__` patch
point), `Settings` is mocked at *both* `src.nodes.llm.base` (read by
`LLMNode.__init__` for `_max_messages_per_node`) and `src.nodes.llm.coder`
(read directly in `__call__` for the `optuna`/`mlflow` run configuration),
and `execute` is mocked at `src.nodes.llm.coder` (its import location) for
every test except the two that deliberately exercise the real subprocess
path (`test_adversarial_column_name_survives_real_codegen_execution`,
`test_unrecognized_feature_spec_operation_raises_loudly`) by calling
`execute` directly with an explicit `timeout`, bypassing `CoderNode`
entirely so no LLM/Settings mocking is needed for those two.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, OptunaConfig, Settings, WorkspaceConfig
from src.nodes.llm.coder import _MAX_EXECUTION_RETRIES, CoderNode
from src.state import new_state
from src.tools.code_executor import ExecResult, execute

_EXP_DIR = "experiments/exp_0"
_VALID_RESPONSE = "```python\nprint('training')\n```"
_VALID_RESPONSE_2 = "```python\nprint('training v2')\n```"


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    settings.optuna = OptunaConfig(n_trials=7, early_stopping_patience=3)
    settings.workspace = WorkspaceConfig(
        root="/competitions",
        chroma_host="chroma",
        chroma_port=8000,
        mlflow_tracking_uri="http://mlflow:5000",
    )
    return settings


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=_VALID_RESPONSE)
    return llm


@pytest.fixture
def patched_llm_factory(mock_llm: MagicMock):
    with patch("src.nodes.llm.base.LLMFactory") as mock_factory:
        mock_factory.get.return_value = mock_llm
        yield mock_factory


@pytest.fixture
def patched_settings():
    with patch("src.nodes.llm.base.Settings") as mock_settings_cls:
        mock_settings_cls.load.return_value = _make_settings()
        yield mock_settings_cls


@pytest.fixture
def patched_coder_settings():
    with patch("src.nodes.llm.coder.Settings") as mock_settings_cls:
        mock_settings_cls.load.return_value = _make_settings()
        yield mock_settings_cls


def _build_state(
    tmp_path: Path,
    *,
    current_iteration: int = 0,
    messages: list[Any] | None = None,
    experiments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    state = new_state("comp", str(tmp_path))
    state["current_iteration"] = current_iteration
    if messages is not None:
        state["messages"] = messages
    if experiments is not None:
        state["experiments"] = experiments
    return state


def _seed_design(
    tmp_path: Path, *, model_family: str = "lightgbm", exp_dir: str = _EXP_DIR
) -> None:
    design_dir = tmp_path / exp_dir
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "design.json").write_text(
        json.dumps({"model_family": model_family, "search_space": {"n_estimators": [100, 500]}}),
        encoding="utf-8",
    )


def _seed_baseline_design(tmp_path: Path, *, target_column: str = "target") -> None:
    baseline_dir = tmp_path / "experiments" / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / "design.json").write_text(
        json.dumps({"target_column": target_column, "model": "gradient_boosting"}),
        encoding="utf-8",
    )


def _write_success_artifacts(
    cwd: str,
    exp_dir: str,
    *,
    cv_score: float = 0.8,
    metric: str | None = None,
    oof_path: str | None = None,
    write_submission: bool = True,
    write_oof: bool = True,
) -> None:
    exp_path = Path(cwd) / exp_dir
    exp_path.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"cv_score": cv_score}
    if metric is not None:
        results["metric"] = metric
    if oof_path is not None:
        results["oof_path"] = oof_path
        oof_file = Path(cwd) / oof_path
        oof_file.parent.mkdir(parents=True, exist_ok=True)
        oof_file.write_bytes(b"oof")
    elif write_oof:
        (exp_path / "oof_predictions.parquet").write_bytes(b"oof")
    (exp_path / "results.json").write_text(json.dumps(results), encoding="utf-8")
    if write_submission:
        (exp_path / "submission.csv").write_text("id,target\n1,0\n", encoding="utf-8")


def _success_execute(exp_dir: str = _EXP_DIR, **kwargs: Any):
    def _side_effect(code: str, cwd: str) -> ExecResult:
        _write_success_artifacts(cwd, exp_dir, **kwargs)
        return ExecResult(returncode=0, stdout="", stderr="", timed_out=False)

    return _side_effect


def _failing_execute(stderr: str = "boom") -> Any:
    def _side_effect(code: str, cwd: str) -> ExecResult:
        return ExecResult(returncode=1, stdout="", stderr=stderr, timed_out=False)

    return _side_effect


# -- config / prompt load (Done-when) --


def test_config_agent_yaml_and_prompt_load() -> None:
    config = load_agent_config("coder")

    assert config.name == "coder"
    assert config.model_role == "implementation"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "experiments/exp_{iteration}/train.py"
    assert config.max_tokens == 8192

    prompt = PromptLoader().load("coder", "v1")
    assert prompt.strip() != ""
    assert prompt.splitlines()[0] == "# System prompt — coder"


def test_prompt_includes_fit_scope_and_column_safety_instructions() -> None:
    prompt = PromptLoader().load("coder", "v1")

    assert "per_fold" in prompt
    assert "fitted inside the CV loop" in prompt
    assert "repr(" in prompt
    assert "json.dumps(" in prompt
    assert "never string-concatenate" in prompt.lower()


def test_prompt_requires_unconditional_target_column_exclusion() -> None:
    prompt = PromptLoader().load("coder", "v1")

    assert "## Target column" in prompt
    assert "unconditional" in prompt.lower()
    assert "every" in prompt.lower() and "branch" in prompt.lower()


# -- happy path / artifact contract --


def test_writes_train_py_results_json_and_submission_csv(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()) as mock_execute:
        node = CoderNode()
        delta = node(_build_state(tmp_path))

    mock_execute.assert_called_once()
    assert (tmp_path / _EXP_DIR / "train.py").read_text(encoding="utf-8") == "print('training')"
    assert (tmp_path / _EXP_DIR / "results.json").is_file()
    assert (tmp_path / _EXP_DIR / "submission.csv").is_file()

    assert len(delta["messages"]) == 1
    assert delta["experiments"] == [
        {
            "id": "exp_0",
            "path": _EXP_DIR,
            "cv_score": 0.8,
            "iteration": 0,
            "model": "lightgbm",
        }
    ]


def test_oof_predictions_default_convention(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 1
    assert (tmp_path / _EXP_DIR / "oof_predictions.parquet").is_file()


def test_oof_predictions_explicit_oof_path(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)
    custom_oof = f"{_EXP_DIR}/custom_oof.parquet"

    with patch(
        "src.nodes.llm.coder.execute",
        side_effect=_success_execute(oof_path=custom_oof, write_oof=False),
    ):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 1
    assert (tmp_path / custom_oof).is_file()
    assert not (tmp_path / _EXP_DIR / "oof_predictions.parquet").is_file()


def test_experiments_entry_model_field_from_design_model_family(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path, model_family="catboost")

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()):
        node = CoderNode()
        delta = node(_build_state(tmp_path))

    assert delta["experiments"][0]["model"] == "catboost"


def test_missing_design_json_degrades_gracefully(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    """No `design.json` on disk at all: the node must still call the LLM
    (never raise before that point) with a degraded placeholder in the
    prompt text, and still succeed end to end."""
    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()):
        node = CoderNode()
        delta = node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 1
    sent_message = mock_llm.invoke.call_args_list[0][0][0][-1]
    assert "design.json not available" in str(sent_message.content)
    assert delta["experiments"][0]["model"] == "unknown"


# -- target column --


def test_target_column_read_from_baseline_design_reaches_prompt(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    """The target column named in `experiments/baseline/design.json` (the
    only place it is ever written, by Phase 3's `baseline_designer`) must be
    read and threaded into the message the LLM receives, under its own
    labeled section."""
    _seed_design(tmp_path)
    _seed_baseline_design(tmp_path, target_column="SalePrice")

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 1
    sent_message = mock_llm.invoke.call_args_list[0][0][0][-1]
    content = str(sent_message.content)
    assert "## Target column" in content
    assert "SalePrice" in content


def test_missing_baseline_design_degrades_to_placeholder_without_raising(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    """No `experiments/baseline/design.json` on disk at all: the node must
    still call the LLM (never raise before that point) with a degraded
    placeholder standing in for the target column."""
    _seed_design(tmp_path)

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 1
    sent_message = mock_llm.invoke.call_args_list[0][0][0][-1]
    content = str(sent_message.content)
    assert "## Target column" in content
    assert "target_column not available from experiments/baseline/design.json" in content


def test_malformed_baseline_design_degrades_to_placeholder_without_raising(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    """`experiments/baseline/design.json` exists but is not valid JSON — same
    degrade-not-raise contract as the missing-file case."""
    _seed_design(tmp_path)
    baseline_dir = tmp_path / "experiments" / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / "design.json").write_text("{not valid json", encoding="utf-8")

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 1
    sent_message = mock_llm.invoke.call_args_list[0][0][0][-1]
    content = str(sent_message.content)
    assert "target_column not available from experiments/baseline/design.json" in content


def test_baseline_design_not_a_json_object_degrades_to_placeholder(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    """`experiments/baseline/design.json` parses as valid JSON but its
    top-level value is a list, not an object — still degrades rather than
    raising or propagating a non-dict value."""
    _seed_design(tmp_path)
    baseline_dir = tmp_path / "experiments" / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / "design.json").write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()):
        node = CoderNode()
        node(_build_state(tmp_path))

    sent_message = mock_llm.invoke.call_args_list[0][0][0][-1]
    assert "target_column not available from experiments/baseline/design.json" in str(
        sent_message.content
    )


def test_baseline_design_missing_target_column_field_degrades_to_placeholder(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    """`experiments/baseline/design.json` is a valid object but has no usable
    `target_column` string — still degrades rather than sending an empty or
    non-string value to the LLM."""
    _seed_design(tmp_path)
    baseline_dir = tmp_path / "experiments" / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    (baseline_dir / "design.json").write_text(
        json.dumps({"model": "gradient_boosting"}), encoding="utf-8"
    )

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()):
        node = CoderNode()
        node(_build_state(tmp_path))

    sent_message = mock_llm.invoke.call_args_list[0][0][0][-1]
    assert "target_column not available from experiments/baseline/design.json" in str(
        sent_message.content
    )


def test_appends_whole_experiments_list_not_just_new_entry(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)
    existing_entry = {
        "id": "exp_prior",
        "path": "experiments/exp_prior",
        "cv_score": 0.5,
        "iteration": -1,
        "model": "xgboost",
    }

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()):
        node = CoderNode()
        delta = node(_build_state(tmp_path, experiments=[existing_entry]))

    assert len(delta["experiments"]) == 2
    assert delta["experiments"][0] == existing_entry
    assert delta["experiments"][1]["id"] == "exp_0"


# -- execution-retry mechanics --


def test_execution_error_triggers_bounded_reprompt_and_retry(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)
    mock_llm.invoke.side_effect = [
        AIMessage(content=_VALID_RESPONSE),
        AIMessage(content=_VALID_RESPONSE_2),
    ]
    calls = {"count": 0}

    def _dispatch(code: str, cwd: str) -> ExecResult:
        calls["count"] += 1
        if calls["count"] == 1:
            return ExecResult(returncode=1, stdout="", stderr="Traceback: boom", timed_out=False)
        return _success_execute()(code, cwd)

    with patch("src.nodes.llm.coder.execute", side_effect=_dispatch):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 2
    second_call_messages = mock_llm.invoke.call_args_list[1][0][0]
    assert any("Traceback: boom" in str(m.content) for m in second_call_messages)
    assert (tmp_path / _EXP_DIR / "train.py").read_text(encoding="utf-8") == "print('training v2')"


def test_exhausts_retries_and_raises(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)
    mock_llm.invoke.return_value = AIMessage(content=_VALID_RESPONSE)

    with patch("src.nodes.llm.coder.execute", side_effect=_failing_execute()):
        node = CoderNode()
        with pytest.raises(ValueError, match="failed after"):
            node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == _MAX_EXECUTION_RETRIES + 1


def test_multiple_code_fences_triggers_retry(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)
    two_fences = "```python\nprint('a')\n```\n\nAlternative:\n\n```python\nprint('b')\n```"
    mock_llm.invoke.side_effect = [
        AIMessage(content=two_fences),
        AIMessage(content=_VALID_RESPONSE),
    ]

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()) as mock_execute:
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 2
    mock_execute.assert_called_once()
    second_call_messages = mock_llm.invoke.call_args_list[1][0][0]
    assert any("expected exactly one" in str(m.content) for m in second_call_messages)


def test_execution_timeout_triggers_retry(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)
    mock_llm.invoke.side_effect = [
        AIMessage(content=_VALID_RESPONSE),
        AIMessage(content=_VALID_RESPONSE_2),
    ]
    calls = {"count": 0}

    def _dispatch(code: str, cwd: str) -> ExecResult:
        calls["count"] += 1
        if calls["count"] == 1:
            return ExecResult(returncode=-1, stdout="", stderr="", timed_out=True)
        return _success_execute()(code, cwd)

    with patch("src.nodes.llm.coder.execute", side_effect=_dispatch):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 2
    second_call_messages = mock_llm.invoke.call_args_list[1][0][0]
    assert any("timed out" in str(m.content) for m in second_call_messages)


def test_oof_artifact_missing_entirely_triggers_retry(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    """Neither the fallback filename nor a `results.json['oof_path']` points at
    a real file on disk — `_oof_artifact_exists` must fail the run."""
    _seed_design(tmp_path)
    mock_llm.invoke.side_effect = [
        AIMessage(content=_VALID_RESPONSE),
        AIMessage(content=_VALID_RESPONSE_2),
    ]
    calls = {"count": 0}

    def _dispatch(code: str, cwd: str) -> ExecResult:
        calls["count"] += 1
        if calls["count"] == 1:
            _write_success_artifacts(cwd, _EXP_DIR, write_oof=False)
        else:
            _write_success_artifacts(cwd, _EXP_DIR)
        return ExecResult(returncode=0, stdout="", stderr="", timed_out=False)

    with patch("src.nodes.llm.coder.execute", side_effect=_dispatch):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 2
    second_call_messages = mock_llm.invoke.call_args_list[1][0][0]
    assert any("OOF predictions artifact" in str(m.content) for m in second_call_messages)


def test_malformed_llm_response_missing_code_fence_triggers_retry(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)
    mock_llm.invoke.side_effect = [
        AIMessage(content="Sorry, here is some narrative with no code fence at all."),
        AIMessage(content=_VALID_RESPONSE),
    ]

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()) as mock_execute:
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 2
    # The first attempt never reached execution — no fenced code to run.
    mock_execute.assert_called_once()
    assert (tmp_path / _EXP_DIR / "train.py").read_text(encoding="utf-8") == "print('training')"


def test_invalid_cv_score_in_results_json_triggers_retry(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)
    mock_llm.invoke.side_effect = [
        AIMessage(content=_VALID_RESPONSE),
        AIMessage(content=_VALID_RESPONSE_2),
    ]

    def _write_invalid_cv_score(cwd: str) -> ExecResult:
        exp_path = Path(cwd) / _EXP_DIR
        exp_path.mkdir(parents=True, exist_ok=True)
        (exp_path / "results.json").write_text(
            json.dumps({"cv_score": "not-a-number"}), encoding="utf-8"
        )
        (exp_path / "submission.csv").write_text("id,target\n1,0\n", encoding="utf-8")
        (exp_path / "oof_predictions.parquet").write_bytes(b"oof")
        return ExecResult(returncode=0, stdout="", stderr="", timed_out=False)

    calls = {"count": 0}

    def _dispatch(code: str, cwd: str) -> ExecResult:
        calls["count"] += 1
        if calls["count"] == 1:
            return _write_invalid_cv_score(cwd)
        return _success_execute()(code, cwd)

    with patch("src.nodes.llm.coder.execute", side_effect=_dispatch):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 2
    assert calls["count"] == 2


def test_missing_submission_csv_triggers_retry(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)
    mock_llm.invoke.side_effect = [
        AIMessage(content=_VALID_RESPONSE),
        AIMessage(content=_VALID_RESPONSE_2),
    ]
    calls = {"count": 0}

    def _dispatch(code: str, cwd: str) -> ExecResult:
        calls["count"] += 1
        if calls["count"] == 1:
            _write_success_artifacts(cwd, _EXP_DIR, write_submission=False)
        else:
            _write_success_artifacts(cwd, _EXP_DIR)
        return ExecResult(returncode=0, stdout="", stderr="", timed_out=False)

    with patch("src.nodes.llm.coder.execute", side_effect=_dispatch):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 2
    assert (tmp_path / _EXP_DIR / "submission.csv").is_file()


def test_metric_field_validated_when_present(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)
    mock_llm.invoke.side_effect = [
        AIMessage(content=_VALID_RESPONSE),
        AIMessage(content=_VALID_RESPONSE_2),
    ]
    calls = {"count": 0}

    def _dispatch(code: str, cwd: str) -> ExecResult:
        calls["count"] += 1
        if calls["count"] == 1:
            _write_success_artifacts(cwd, _EXP_DIR, metric="not_a_real_metric")
        else:
            _write_success_artifacts(cwd, _EXP_DIR, metric="R-Squared")
        return ExecResult(returncode=0, stdout="", stderr="", timed_out=False)

    with patch("src.nodes.llm.coder.execute", side_effect=_dispatch):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 2
    second_call_messages = mock_llm.invoke.call_args_list[1][0][0]
    assert any("not_a_real_metric" in str(m.content) for m in second_call_messages)


def test_metric_separator_variant_passes_without_retry(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    _seed_design(tmp_path)

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute(metric="R-Squared")):
        node = CoderNode()
        node(_build_state(tmp_path))

    assert mock_llm.invoke.call_count == 1


# -- OOF artifact symlink containment --


def test_oof_artifact_symlink_escaping_workspace_is_rejected(tmp_path: Path) -> None:
    """A `results.json['oof_path']` that names a symlink living inside the
    experiment directory, but whose target resolves outside the workspace
    root, must be rejected — even though the raw path itself is relative and
    contains no `..` component, so the earlier string-level checks alone
    would let it through."""
    from src.nodes.llm.coder import _oof_artifact_exists
    from src.workspace.workspace_manager import WorkspaceManager

    workspace_root = tmp_path / "workspace"
    workspace = WorkspaceManager(str(workspace_root))
    exp_dir = "experiments/exp_0"
    (workspace_root / exp_dir).mkdir(parents=True, exist_ok=True)

    outside_target = tmp_path / "outside" / "secret.parquet"
    outside_target.parent.mkdir(parents=True, exist_ok=True)
    outside_target.write_bytes(b"oof")

    symlink_path = workspace_root / exp_dir / "oof_predictions.parquet"
    symlink_path.symlink_to(outside_target)

    results = {"oof_path": f"{exp_dir}/oof_predictions.parquet"}

    assert _oof_artifact_exists(workspace, exp_dir, results) is False


def test_oof_artifact_symlink_within_workspace_is_accepted(tmp_path: Path) -> None:
    """A symlink is not rejected outright — only one that resolves outside
    the workspace root. A symlink whose target is also inside the workspace
    must still be treated as present."""
    from src.nodes.llm.coder import _oof_artifact_exists
    from src.workspace.workspace_manager import WorkspaceManager

    workspace_root = tmp_path / "workspace"
    workspace = WorkspaceManager(str(workspace_root))
    exp_dir = "experiments/exp_0"
    (workspace_root / exp_dir).mkdir(parents=True, exist_ok=True)

    inside_target = workspace_root / exp_dir / "real_oof.parquet"
    inside_target.write_bytes(b"oof")

    symlink_path = workspace_root / exp_dir / "oof_predictions.parquet"
    symlink_path.symlink_to(inside_target)

    results = {"oof_path": f"{exp_dir}/oof_predictions.parquet"}

    assert _oof_artifact_exists(workspace, exp_dir, results) is True


# -- critic-feedback threading --


def test_second_invocation_with_critic_feedback_produces_different_code(
    patched_llm_factory, patched_settings, patched_coder_settings, mock_llm, tmp_path: Path
) -> None:
    feedback_text = "code_critic: rewrite the seeding to match fold_config.json's seed."

    def _llm_side_effect(messages: list[Any]) -> AIMessage:
        if any(feedback_text in str(m.content) for m in messages):
            return AIMessage(content=_VALID_RESPONSE_2)
        return AIMessage(content=_VALID_RESPONSE)

    mock_llm.invoke.side_effect = _llm_side_effect
    _seed_design(tmp_path)

    with patch("src.nodes.llm.coder.execute", side_effect=_success_execute()):
        node = CoderNode()
        node(_build_state(tmp_path, messages=[]))
        first_code = (tmp_path / _EXP_DIR / "train.py").read_text(encoding="utf-8")

        node(_build_state(tmp_path, messages=[AIMessage(content=feedback_text)]))
        second_code = (tmp_path / _EXP_DIR / "train.py").read_text(encoding="utf-8")

    assert first_code != second_code
    assert first_code == "print('training')"
    assert second_code == "print('training v2')"

    second_call_messages = mock_llm.invoke.call_args_list[-1][0][0]
    assert any(
        isinstance(m, AIMessage) and feedback_text in str(m.content) for m in second_call_messages
    )


# -- real-subprocess codegen-contract tests (no LLM/Settings mocking) --

_ADVERSARIAL_COLUMN = 'He said "hello" \\ world'


def _build_column_safety_reference_script(column_name: str) -> str:
    """A hand-written script implementing the prompt's column-safety
    contract: the adversarial column name reaches Python source only through
    `repr()`, never string-concatenated into a quoted literal."""
    return (
        "import pandas as pd\n"
        "from pathlib import Path\n\n"
        "def main() -> None:\n"
        "    df = pd.read_csv(Path('data/adversarial.csv'))\n"
        f"    column_name = {column_name!r}\n"
        "    df[column_name] = df[column_name].fillna(0)\n"
        "    print('ok')\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def test_adversarial_column_name_survives_real_codegen_execution(tmp_path: Path) -> None:
    import csv

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "adversarial.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([_ADVERSARIAL_COLUMN, "target"])
        writer.writerow([1, 0])
        writer.writerow([2, 1])

    script = _build_column_safety_reference_script(_ADVERSARIAL_COLUMN)

    result = execute(script, cwd=str(tmp_path), timeout=30)

    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


_KNOWN_FIT_SCOPE_OPERATIONS = ("log_transform", "median_impute")

_FIT_SCOPE_REFERENCE_SCRIPT = (
    "import json\n"
    "from pathlib import Path\n\n"
    "def main() -> None:\n"
    "    spec = json.loads(Path('design/iteration_0/feature_spec.json').read_text())\n"
    "    for entry in spec['features']:\n"
    "        operation = entry['operation']\n"
    "        if operation == 'log_transform':\n"
    "            pass\n"
    "        elif operation == 'median_impute':\n"
    "            pass\n"
    "        else:\n"
    "            raise ValueError(f'unrecognized feature_spec operation: {operation!r}')\n"
    "    print('ok')\n\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)


def test_unrecognized_feature_spec_operation_raises_loudly(tmp_path: Path) -> None:
    design_dir = tmp_path / "design" / "iteration_0"
    design_dir.mkdir(parents=True, exist_ok=True)
    unrecognized_operation = "quantile_binning"
    (design_dir / "feature_spec.json").write_text(
        json.dumps(
            {
                "features": [
                    {
                        "columns": ["num1"],
                        "operation": unrecognized_operation,
                        "params": {},
                        "fit_scope": "per_fold",
                        "rationale": "not a known operation",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = execute(_FIT_SCOPE_REFERENCE_SCRIPT, cwd=str(tmp_path), timeout=30)

    assert result.returncode != 0
    assert unrecognized_operation in result.stderr
