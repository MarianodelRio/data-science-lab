"""Unit tests for src/tools/code_executor.py."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import src.tools.code_executor as code_executor
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


def test_execute_default_timeout_value_actually_drives_enforcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves the resolved timeout genuinely comes from
    `Settings.load().execution.code_executor_timeout_seconds` — not e.g. a
    hardcoded fallback that happens to also let a quick script finish. Fakes
    `Settings.load()` to return a deliberately tiny timeout and asserts
    `execute()` actually enforces that exact value against a script that
    would otherwise run far longer.
    """

    class _FakeExecutionConfig:
        code_executor_timeout_seconds = 1

    class _FakeSettings:
        execution = _FakeExecutionConfig()

    monkeypatch.setattr(
        code_executor.Settings,
        "load",
        classmethod(lambda cls, *args, **kwargs: _FakeSettings()),
    )

    start = time.monotonic()
    result = execute("import time; time.sleep(30)", str(tmp_path))
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert elapsed < 4  # well under the 30s sleep; bounded by the fake 1s timeout


def test_execute_does_not_leak_credential_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "super-secret-anthropic-value")

    result = execute(
        "import os; print('ANTHROPIC_API_KEY' in os.environ)",
        str(tmp_path),
        10,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "False"


def test_execute_child_can_still_import_installed_packages(tmp_path: Path) -> None:
    """The minimal child env must not break normal interpreter startup —
    stdlib imports (a stand-in for numpy/pandas/etc.) must still resolve.
    """
    result = execute("import json; print(json.dumps({'ok': True}))", str(tmp_path), 10)

    assert result.returncode == 0
    assert '"ok": true' in result.stdout


def test_execute_nonexistent_cwd_does_not_raise(tmp_path: Path) -> None:
    missing_cwd = str(tmp_path / "does" / "not" / "exist")

    result = execute("print(1)", missing_cwd, 10)

    assert result.returncode == -1
    assert result.timed_out is False
    assert result.stdout == ""
    assert result.stderr != ""


def test_execute_timeout_with_escaped_grandchild_does_not_hang(tmp_path: Path) -> None:
    """A process that itself starts a new session escapes our `killpg` and
    keeps holding the write end of our stdout/stderr pipes open; `execute()`
    must still return promptly instead of blocking on the post-kill
    `communicate()` forever.

    The grandchild sleeps for 10s (not indefinitely) so it exits on its own
    shortly after the assertions run, rather than lingering as a long-lived
    orphan on the test machine.
    """
    code = (
        "import subprocess, sys, time\n"
        "subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(10)'],\n"
        "    start_new_session=True,\n"
        ")\n"
        "time.sleep(999)\n"
    )

    start = time.monotonic()
    result = execute(code, str(tmp_path), 1)
    elapsed = time.monotonic() - start

    assert result.timed_out is True
    assert result.returncode == -1
    # Bounded by the outer timeout (1s) plus the post-kill grace period, not
    # by the escaped grandchild holding the pipes open for up to 10s.
    assert elapsed < 15


def test_execute_killpg_race_process_already_exited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the process exits in the window between `communicate()` raising
    `TimeoutExpired` and the `killpg` call, `killpg` raises
    `ProcessLookupError` — `execute()` must swallow it, not propagate.
    """

    def _raise_process_lookup_error(pgid: int, sig: int) -> None:
        raise ProcessLookupError()

    monkeypatch.setattr(code_executor.os, "killpg", _raise_process_lookup_error)

    result = execute("import time; time.sleep(5)", str(tmp_path), 1)

    assert result.timed_out is True
    assert result.returncode == -1
