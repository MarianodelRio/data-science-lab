---
id: T-006
phase: 1
agent: infra-agent
depends_on: [T-001]
status: available
folders: ["src/tools/"]
outputs: [code_executor.execute(code, cwd, timeout) -> ExecResult]
size: S
branch: ~
pr: ~
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
