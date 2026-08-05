"""Runs arbitrary Python source in a subprocess and captures the result.

`execute` never raises — a missing/invalid `cwd`, a nonzero exit code, and a
timeout (including one where the executed code itself escapes its process
group) all come back as a well-formed `ExecResult` instead of propagating an
exception.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

from src.config.settings import Settings

# Grace period for the post-kill `communicate()` call. If the executed code
# spawns its own subprocess with `start_new_session=True`/`os.setsid()`,
# that grandchild leaves our process group and survives `killpg` — but it
# still holds the write end of our stdout/stderr pipes open (fds 1/2 are
# inherited across `close_fds=True`, which only closes fds >= 3). An
# untimed `communicate()` after the kill would then block until every
# writer closes the pipe, i.e. potentially forever. Bounding it means
# `execute()` always returns.
_POST_KILL_GRACE_SECONDS = 5

# Environment variables allow-listed into the child process. Deliberately
# NOT `os.environ` wholesale: this process holds provider/Kaggle credentials
# (ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, GROQ_API_KEY, KAGGLE_USERNAME,
# KAGGLE_KEY, ...), and `ExecResult.stdout`/`stderr` get persisted to local
# logs and fed into the RAG store — an accidental or prompt-injected
# `os.environ` dump in executed code must not be able to leak them.
_CHILD_ENV_PASSTHROUGH = ("HOME", "LANG", "LC_ALL", "VIRTUAL_ENV", "PYTHONPATH")


def _build_child_env() -> dict[str, str]:
    """Build a minimal environment for the executed subprocess.

    Only what a normal Python interpreter needs to start and import
    already-installed packages (numpy, pandas, ...) is passed through;
    credentials and every other ambient variable are dropped.
    """
    env = {"PATH": os.environ.get("PATH", os.defpath)}
    for key in _CHILD_ENV_PASSTHROUGH:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env


def _decode(data: bytes | str | None) -> str:
    """Best-effort decode of partial output captured on a `TimeoutExpired`.

    When `communicate()` raises after a timeout, the bytes captured so far
    are attached to the exception's `output`/`stderr` attributes *before*
    the text-mode decoding `communicate()` normally does on a clean return
    — so they arrive as raw `bytes` here, not `str`.
    """
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def execute(code: str, cwd: str, timeout: int | None = None) -> ExecResult:
    """Run `code` as a Python script in `cwd`, enforcing `timeout` seconds.

    `code` is passed via `sys.executable -c <code>` (no temp files). The
    child runs with a minimal, explicit environment (see
    `_build_child_env`) — it does not inherit this process's credentials —
    and in its own process group so that on timeout the whole group can be
    killed (`SIGKILL`); `timed_out=True` is then returned along with a
    `returncode` of `-1` and whatever partial stdout/stderr had been
    produced. A nonzero exit code is never treated as an error here — it is
    simply returned.

    If `timeout` is `None`, it is resolved from
    `Settings.load().execution.code_executor_timeout_seconds`. Passing an
    explicit `timeout` avoids that `Settings.load()` call entirely.
    """
    if timeout is not None:
        resolved_timeout = timeout
    else:
        resolved_timeout = Settings.load().execution.code_executor_timeout_seconds

    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=cwd,
            env=_build_child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError as exc:
        # e.g. cwd doesn't exist / isn't a directory / isn't accessible —
        # raised synchronously by Popen, before there's any process to wait
        # on. Report it as a failed run rather than letting it propagate.
        return ExecResult(returncode=-1, stdout="", stderr=str(exc), timed_out=False)

    try:
        stdout, stderr = proc.communicate(timeout=resolved_timeout)
    except subprocess.TimeoutExpired:
        # Suppress ProcessLookupError: the process may have already exited
        # in the window between communicate() raising and this call —
        # nothing left to kill.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        try:
            stdout, stderr = proc.communicate(timeout=_POST_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired as exc:
            # A grandchild that escaped our process group is still holding
            # the pipes open. Don't wait any longer than the grace period —
            # take whatever partial output was captured and move on.
            stdout = _decode(exc.output)
            stderr = _decode(exc.stderr)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
        return ExecResult(returncode=-1, stdout=stdout, stderr=stderr, timed_out=True)

    return ExecResult(
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
    )
