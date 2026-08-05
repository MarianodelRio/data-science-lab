"""Runs arbitrary Python source in a subprocess and captures the result.

`execute` never raises on a nonzero exit code — callers always get back an
`ExecResult` describing what happened, including whether the process had to
be killed for exceeding its timeout.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass

from src.config.settings import Settings


@dataclass(frozen=True)
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


def execute(code: str, cwd: str, timeout: int | None = None) -> ExecResult:
    """Run `code` as a Python script in `cwd`, enforcing `timeout` seconds.

    `code` is passed via `sys.executable -c <code>` (no temp files). The
    process runs in its own process group; if it exceeds `timeout`, the
    whole group is killed (`SIGKILL`) so no orphaned children survive, and
    `timed_out=True` is returned along with a `returncode` of `-1` and
    whatever partial stdout/stderr had been produced. A nonzero exit code
    is never treated as an error here — it is simply returned.

    If `timeout` is `None`, it is resolved from
    `Settings.load().execution.code_executor_timeout_seconds`. Passing an
    explicit `timeout` avoids that `Settings.load()` call entirely.
    """
    if timeout is not None:
        resolved_timeout = timeout
    else:
        resolved_timeout = Settings.load().execution.code_executor_timeout_seconds

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=resolved_timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
        return ExecResult(returncode=-1, stdout=stdout, stderr=stderr, timed_out=True)

    return ExecResult(
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
    )
