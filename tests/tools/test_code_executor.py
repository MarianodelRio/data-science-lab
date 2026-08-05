"""Unit tests for src/tools/code_executor.py."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.tools.code_executor import ExecResult, execute


def test_execute_success_captures_stdout(tmp_path: Path) -> None:
    result = execute("print(2+2)", str(tmp_path), 10)

    assert isinstance(result, ExecResult)
    assert result.returncode == 0
    assert "4" in result.stdout
    assert result.timed_out is False


def test_execute_nonzero_exit_does_not_raise(tmp_path: Path) -> None:
    result = execute("import sys; sys.exit(3)", str(tmp_path), 10)

    assert result.returncode == 3
    assert result.timed_out is False


def test_execute_timeout_kills_process_and_sets_flag(tmp_path: Path) -> None:
    start = time.monotonic()
    result = execute("import time; time.sleep(5)", str(tmp_path), 1)
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert result.returncode == -1
    # No-orphan proxy: if the process group weren't killed, communicate() would
    # block until the full 5s sleep finished (or longer). Well under that bound
    # means the process was actually reaped promptly, not just abandoned.
    assert elapsed < 3


def test_execute_captures_stderr_on_exception(tmp_path: Path) -> None:
    result = execute("raise ValueError('boom')", str(tmp_path), 10)

    assert result.returncode != 0
    assert "ValueError" in result.stderr
    assert "boom" in result.stderr


def test_execute_default_timeout_uses_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-test-key")
    monkeypatch.setenv("KAGGLE_USERNAME", "kaggle-test-user")
    monkeypatch.setenv("KAGGLE_KEY", "kaggle-test-key")

    result = execute("print(2+2)", str(tmp_path))

    assert result.returncode == 0
    assert "4" in result.stdout
    assert result.timed_out is False
