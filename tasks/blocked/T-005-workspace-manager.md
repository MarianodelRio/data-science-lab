---
id: T-005
phase: 0
agent: infra-agent
depends_on: [T-001]
status: blocked
folders: ["src/workspace/"]
outputs: [WorkspaceManager read/write json/text/notebook, experiment_dir, ensure_dir]
size: M
branch: ~
pr: ~
---

## WorkspaceManager (src/workspace/)

**Scope:** `src/workspace/` only. **Shared contract** — the sole file-I/O point to the ML workspace.

**Delivers:**
- `WorkspaceManager(workspace_path)` with the API in `design.md` § WorkspaceManager API: `read_json`, `write_json`, `read_text`, `write_text`, `write_notebook`, `experiment_dir`, `ensure_dir`
- All writes create parent dirs; all methods take **relative** paths and reject absolute paths / `..` traversal
- `write_*` returns the absolute path written
- `write_notebook(path, cells)` produces a valid `.ipynb` (nbformat)

**Done when:**
- [ ] `write_json("reports/eda.json", {...})` creates the file and returns its absolute path
- [ ] `read_json` round-trips the written dict exactly
- [ ] a path containing `..` raises `ValueError`
- [ ] `write_notebook` output loads with `nbformat.read` without error
- [ ] tests use `tmp_path` — no writes outside the temp dir
- [ ] `mypy src/workspace/` passes
- [ ] `docs/pipeline.md` "Workspace I/O" section updated
