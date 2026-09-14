---
id: T-049
phase: 6
agent: api-agent
depends_on: [T-034, T-048]
status: pr-open
folders: [src/api/]
outputs: ["GET /api/runs/{run_id}/experiments", "GET /api/runs/{run_id}/files/{path}"]
size: M
branch: feature/T-049-experiments-file-endpoints
pr: https://github.com/MarianodelRio/data-science-lab/pull/50
---

## Experiments + file-content endpoints; validate API keys at startup

**Scope:** `src/api/`. Two frontend components are built and tested but have no
data source: `ExperimentsTable` (no endpoint exposes `LabState.experiments`)
and `FileViewer` (no endpoint serves workspace file content). Separately, the
API should fail fast on startup if required provider keys are missing, using
T-048's new validation.

**Delivers:**
- `GET /api/runs/{run_id}/experiments` — returns `LabState.experiments` for
  the run (404 on unknown run), each experiment with a stable unique `id`
- An endpoint serving workspace file content for a given run (e.g.
  `GET /api/runs/{run_id}/files/{path}`), path-traversal-guarded via
  `WorkspaceManager`
- Call `Settings.validate_required_keys()` at API startup (`src/api/main.py`)
  so a misconfigured deployment fails immediately with a clear error

**Done when:**
- [ ] both endpoints return correct data and 404 on unknown run
- [ ] file endpoint rejects path traversal / absolute paths
- [ ] API fails to start with a clear error when required keys are missing
      (integration test)
- [ ] tests written and passing (types per the Testing strategy in `design.md`)
- [ ] `docs/api.md` updated
