"""Unit tests for `JsonlCallbackHandler`.

All file I/O goes through `tmp_path` — no test touches the real repo `runs/`
directory (the one exception, `test_default_runs_dir_matches_repo_root_runs`,
only checks the computed path and never calls a write method).
"""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from src.observability.jsonl_callback import RUNS_DIR, JsonlCallbackHandler

SCHEMA_KEYS = {
    "timestamp",
    "run_id",
    "iteration",
    "phase",
    "node",
    "event",
    "duration_ms",
    "tokens_in",
    "tokens_out",
    "model",
    "output_summary",
}


def _read_lines(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def test_on_chain_start_then_on_chain_end_writes_two_lines(tmp_path: Path) -> None:
    handler = JsonlCallbackHandler("run-1", runs_dir=tmp_path)
    run_id = uuid4()

    inputs = {"current_iteration": 1, "phase": "phase4_design"}
    handler.on_chain_start({}, inputs, run_id=run_id, name="solution_architect")
    handler.on_chain_end({"messages": []}, run_id=run_id)

    lines = _read_lines(handler._log_path)
    assert len(lines) == 2
    assert lines[0]["event"] == "start"
    assert lines[1]["event"] == "end"


def test_every_line_has_all_schema_keys(tmp_path: Path) -> None:
    handler = JsonlCallbackHandler("run-2", runs_dir=tmp_path)
    run_id = uuid4()

    handler.on_chain_start({}, {}, run_id=run_id, name="node_a")
    handler.on_chain_end({}, run_id=run_id)

    lines = _read_lines(handler._log_path)
    assert len(lines) == 2
    for line in lines:
        assert set(line) == SCHEMA_KEYS


def test_duration_ms_is_none_on_start_and_populated_on_end(tmp_path: Path) -> None:
    handler = JsonlCallbackHandler("run-3", runs_dir=tmp_path)
    run_id = uuid4()

    handler.on_chain_start({}, {}, run_id=run_id, name="node_a")
    handler.on_chain_end({}, run_id=run_id)

    lines = _read_lines(handler._log_path)
    assert lines[0]["duration_ms"] is None
    assert isinstance(lines[1]["duration_ms"], int)
    assert lines[1]["duration_ms"] >= 0


def test_write_failure_is_swallowed_and_warned_on_stderr(tmp_path, capsys) -> None:
    blocked = tmp_path / "blocked-run"
    blocked.write_text("occupied")  # a file, not a directory, at the run's path

    handler = JsonlCallbackHandler("blocked-run", runs_dir=tmp_path)

    handler.on_chain_start({}, {}, run_id=uuid4(), name="node_a")  # must not raise

    err = capsys.readouterr().err
    assert err
    assert "blocked-run" in err


def test_constructor_rejects_empty_run_id() -> None:
    with pytest.raises(ValueError):
        JsonlCallbackHandler("")


def test_constructor_rejects_run_id_with_slash() -> None:
    with pytest.raises(ValueError):
        JsonlCallbackHandler("a/b")


def test_constructor_rejects_run_id_dotdot() -> None:
    with pytest.raises(ValueError):
        JsonlCallbackHandler("..")
    with pytest.raises(ValueError):
        JsonlCallbackHandler("a/../b")


def test_constructor_accepts_normal_run_id(tmp_path: Path) -> None:
    run_id = "a1b2c3d4-0000-0000-0000-000000000000"
    handler = JsonlCallbackHandler(run_id, runs_dir=tmp_path)

    assert handler._log_path == tmp_path / run_id / "execution.jsonl"
    assert not (tmp_path / run_id).exists()


def test_iteration_and_phase_are_read_from_node_inputs(tmp_path: Path) -> None:
    handler = JsonlCallbackHandler("run-4", runs_dir=tmp_path)
    run_id = uuid4()

    handler.on_chain_start(
        {}, {"current_iteration": 3, "phase": "phase4_design"}, run_id=run_id, name="node_a"
    )
    handler.on_chain_end({}, run_id=run_id)

    lines = _read_lines(handler._log_path)
    assert lines[0]["iteration"] == 3
    assert lines[0]["phase"] == "phase4_design"
    assert lines[1]["iteration"] == 3
    assert lines[1]["phase"] == "phase4_design"


def test_llm_call_inside_node_populates_tokens_and_model(tmp_path: Path) -> None:
    handler = JsonlCallbackHandler("run-5", runs_dir=tmp_path)
    node_run_id = uuid4()
    llm_run_id = uuid4()

    handler.on_chain_start({}, {}, run_id=node_run_id, name="solution_architect")
    handler.on_chat_model_start(
        {},
        [[]],
        run_id=llm_run_id,
        parent_run_id=node_run_id,
        invocation_params={"model": "deepseek-v4-flash"},
    )
    response = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="x",
                        usage_metadata={
                            "input_tokens": 3200,
                            "output_tokens": 890,
                            "total_tokens": 4090,
                        },
                    )
                )
            ]
        ]
    )
    handler.on_llm_end(response, run_id=llm_run_id, parent_run_id=node_run_id)
    handler.on_chain_end({"messages": [AIMessage(content="done")]}, run_id=node_run_id)

    lines = _read_lines(handler._log_path)
    end_record = lines[-1]
    assert end_record["tokens_in"] == 3200
    assert end_record["tokens_out"] == 890
    assert end_record["model"] == "deepseek-v4-flash"


def test_llm_call_with_unextractable_usage_reports_null_not_zero(tmp_path: Path) -> None:
    """Bug 3 regression: an LLM call that happened but whose token usage
    couldn't be extracted (e.g. `FakeListChatModel`, which never sets
    `usage_metadata`) must report `tokens_in`/`tokens_out` as `null`, matching
    "no LLM call observed" — not `0`, which would wrongly imply a real,
    zero-token call happened."""
    handler = JsonlCallbackHandler("run-10", runs_dir=tmp_path)
    node_run_id = uuid4()
    llm_run_id = uuid4()

    handler.on_chain_start({}, {}, run_id=node_run_id, name="node_with_unextractable_usage")
    handler.on_chat_model_start(
        {},
        [[]],
        run_id=llm_run_id,
        parent_run_id=node_run_id,
        invocation_params={"model": "some-model"},
    )
    # No usage_metadata on the message, and no llm_output token_usage either —
    # exactly what a bare FakeListChatModel response looks like.
    response = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content="x"))]], llm_output=None
    )
    handler.on_llm_end(response, run_id=llm_run_id, parent_run_id=node_run_id)
    handler.on_chain_end({"messages": [AIMessage(content="done")]}, run_id=node_run_id)

    lines = _read_lines(handler._log_path)
    end_record = lines[-1]
    assert end_record["tokens_in"] is None
    assert end_record["tokens_out"] is None
    assert end_record["model"] == "some-model"  # model is still known even though tokens aren't


def test_on_chain_error_pops_llm_usage_bucket(tmp_path: Path) -> None:
    """Housekeeping fix: `on_chain_error` must also discard any accumulated
    `_llm_usage` bucket for the erroring node's run id, not just `_starts` —
    otherwise a bucket orphans for the process lifetime on retry-heavy runs."""
    handler = JsonlCallbackHandler("run-11", runs_dir=tmp_path)
    node_run_id = uuid4()
    llm_run_id = uuid4()

    handler.on_chain_start({}, {}, run_id=node_run_id, name="node_that_errors")
    handler.on_chat_model_start(
        {}, [[]], run_id=llm_run_id, parent_run_id=node_run_id, invocation_params={"model": "m"}
    )
    usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}
    response = LLMResult(
        generations=[[ChatGeneration(message=AIMessage(content="x", usage_metadata=usage))]]
    )
    handler.on_llm_end(response, run_id=llm_run_id, parent_run_id=node_run_id)
    assert node_run_id in handler._llm_usage

    handler.on_chain_error(RuntimeError("boom"), run_id=node_run_id)

    assert node_run_id not in handler._llm_usage
    assert node_run_id not in handler._starts


def test_compute_node_with_no_llm_call_has_null_tokens_and_model(tmp_path: Path) -> None:
    handler = JsonlCallbackHandler("run-6", runs_dir=tmp_path)
    run_id = uuid4()

    handler.on_chain_start({}, {}, run_id=run_id, name="data_analyst")
    handler.on_chain_end({"eda_report_path": "workspace/eda.md"}, run_id=run_id)

    lines = _read_lines(handler._log_path)
    end_record = lines[-1]
    assert end_record["tokens_in"] is None
    assert end_record["tokens_out"] is None
    assert end_record["model"] is None


def test_output_summary_from_llm_message_content(tmp_path: Path) -> None:
    handler = JsonlCallbackHandler("run-7", runs_dir=tmp_path)
    run_id = uuid4()

    handler.on_chain_start({}, {}, run_id=run_id, name="solution_architect")
    content = "Designed LightGBM + stacking strategy for iteration 2"
    handler.on_chain_end({"messages": [AIMessage(content=content)]}, run_id=run_id)

    lines = _read_lines(handler._log_path)
    assert lines[-1]["output_summary"] == content


def test_output_summary_falls_back_to_updated_keys_for_compute_node(tmp_path: Path) -> None:
    handler = JsonlCallbackHandler("run-8", runs_dir=tmp_path)
    run_id = uuid4()

    handler.on_chain_start({}, {}, run_id=run_id, name="data_analyst")
    handler.on_chain_end({"eda_report_path": "workspace/eda.md"}, run_id=run_id)

    lines = _read_lines(handler._log_path)
    assert lines[-1]["output_summary"] == "updated: eda_report_path"


def test_output_summary_is_truncated(tmp_path: Path) -> None:
    handler = JsonlCallbackHandler("run-9", runs_dir=tmp_path)
    run_id = uuid4()

    long_content = "word " * 100  # far more than 200 chars
    handler.on_chain_start({}, {}, run_id=run_id, name="solution_architect")
    handler.on_chain_end({"messages": [AIMessage(content=long_content)]}, run_id=run_id)

    lines = _read_lines(handler._log_path)
    summary = lines[-1]["output_summary"]
    assert len(summary) <= 201
    assert summary.endswith("…")


def test_default_runs_dir_matches_repo_root_runs() -> None:
    handler = JsonlCallbackHandler("some-run-id")

    assert handler._log_path == RUNS_DIR / "some-run-id" / "execution.jsonl"
