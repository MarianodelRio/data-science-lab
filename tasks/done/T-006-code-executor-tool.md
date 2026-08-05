---
id: T-006
phase: 1
agent: infra-agent
depends_on: [T-001]
status: done
folders: ["src/tools/"]
outputs: [code_executor.execute(code, cwd, timeout) -> ExecResult]
size: S
branch: feature/T-006-code-executor-tool
pr: "https://github.com/MarianodelRio/data-science-lab/pull/7"
---

## code_executor tool (src/tools/code_executor.py)

**Scope:** `src/tools/code_executor.py` only.

**Delivers:**
- `execute(code: str, cwd: str, timeout: int) -> ExecResult` running Python in a subprocess
- `ExecResult` dataclass: `{returncode: int, stdout: str, stderr: str, timed_out: bool}`
- Captures stdout/stderr; enforces timeout (kills process group on expiry, sets `timed_out=True`)
- `timeout` defaults to `settings.execution.code_executor_timeout_seconds`
- Never raises on non-zero exit — returns the result

**Done when:**
- [ ] `execute("print(2+2)", tmp, 10)` returns `returncode==0` and `stdout` contains `"4"`
- [ ] `execute("import sys; sys.exit(3)", tmp, 10)` returns `returncode==3` and does not raise
- [ ] a script sleeping longer than timeout returns `timed_out==True` and is killed (no orphan process)
- [ ] `stderr` is captured on a script that raises
- [ ] `mypy src/tools/code_executor.py` passes
- [ ] `docs/pipeline.md` "Tools" section updated

## Completed

Implemented `src/tools/code_executor.py`: `ExecResult` frozen dataclass
(`returncode`, `stdout`, `stderr`, `timed_out`) + `execute(code, cwd, timeout=None) -> ExecResult`.
Runs `sys.executable -c <code>` via `subprocess.Popen(..., start_new_session=True)`; on timeout,
kills the whole process group (`os.killpg` + `SIGKILL`). `timeout` resolves from
`Settings.load().execution.code_executor_timeout_seconds` only when the caller passes `None`
(explicit timeouts skip `Settings.load()` entirely). Tests in `tests/tools/test_code_executor.py`
(11 tests, real subprocesses, no mocks). `docs/pipeline.md` § Tools updated.

Two rounds of review surfaced and fixed real issues beyond the original spec:

- **Security BLOCKER**: the child subprocess originally inherited the full parent environment
  (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY`, Kaggle creds), and since
  `ExecResult.stdout`/`stderr` get persisted to JSONL logs, this was a durable secret-leak vector
  for LLM-generated code. Fixed with `_build_child_env()` — an explicit allow-list
  (`PATH`, `HOME`, `LANG`, `LC_ALL`, `VIRTUAL_ENV`, `PYTHONPATH`) instead of inheriting `os.environ`.
- **Adversarial HIGH**: the post-kill `communicate()` call had no timeout of its own — if executed
  code spawned its own `start_new_session=True` grandchild, that grandchild survives `killpg`
  (different process group) but still holds the stdout/stderr pipe fds open, so `communicate()`
  would block forever waiting for all writers to close, completely defeating the timeout contract.
  Fixed with `_POST_KILL_GRACE_SECONDS = 5`: the second `communicate()` is bounded, and on a second
  timeout the pipes are closed directly and best-effort partial output is returned.
- **Adversarial MEDIUM**: `Popen(cwd=cwd)` raised uncaught `FileNotFoundError`/`NotADirectoryError`
  if `cwd` didn't exist, breaking the "never raises" contract on an untested path. Fixed by wrapping
  `Popen` construction in `try/except OSError`, returning a well-formed `ExecResult` instead.
- **WARNING** (flagged independently by both code-quality and security): `os.killpg` was unguarded
  against the race where the child exits between `TimeoutExpired` and the kill call
  (`ProcessLookupError`). Fixed with `contextlib.suppress(ProcessLookupError)`.
- Also fixed as a minor bonus: `errors="replace"` added to `Popen`'s text decoding to avoid an
  unhandled `UnicodeDecodeError` on non-UTF-8 subprocess output.

**Deferred, not fixed** (out of scope for this S-sized, contract-only tool): `cwd` is not validated
against a workspace root — `code_executor` has no workspace-root concept passed to it; this belongs
to whichever future task (T-013 data_analyst, T-020 baseline_runner, T-029 coder) wires it to
`WorkspaceManager`. No CPU/memory/process-count resource limits beyond the wall-clock timeout.
`timeout<=0` is not validated (no spec on desired behavior).
