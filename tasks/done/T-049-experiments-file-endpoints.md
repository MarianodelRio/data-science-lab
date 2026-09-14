---
id: T-049
phase: 6
agent: api-agent
depends_on: [T-034, T-048]
status: done
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

## Completed
- What was implemented:
  - `src/api/models.py`: added `ExperimentsResponse` (`experiments`, `baseline_score`,
    `best_experiment_path`), with `ConfigDict(ser_json_inf_nan="null")` so an `inf`/`nan`
    `cv_score` inside `experiments` (not just `baseline_score`) serializes as JSON `null`.
  - `src/api/routers/experiments.py` (new): `GET /api/runs/{run_id}/experiments` — 404 on
    unknown/malformed `run_id` via `registry.read_run_record`, otherwise reads the live
    checkpoint via `runs._get_or_build_graph_values` and reports `baseline_score` as `null`
    unless `baseline_results_path` is set.
  - `src/api/routers/files.py` (new): `GET /api/runs/{run_id}/files/{path:path}` — 404 on
    unknown run, reads the file via `WorkspaceManager.read_text` off the event loop
    (`asyncio.to_thread`), mapping `ValueError` (empty/absolute/`..` path) to 400,
    `FileNotFoundError`/`IsADirectoryError` to 404, and `UnicodeDecodeError` to 415. Content is
    always served as `text/plain; charset=utf-8`, no extension-based content-type sniffing.
  - `src/api/main.py`: added a `key_validator: KeyValidator | None = None` param to
    `create_app`, defaulting to `Settings.validate_required_keys`; wired it into a new
    `@asynccontextmanager` lifespan (`await asyncio.to_thread(resolved_key_validator)` before
    `yield`) so a missing/empty required key aborts startup with `ConfigError` — never at
    import time or from calling `create_app` itself, only when the ASGI lifespan actually
    starts. Registered `experiments_router` and `files_router`.
  - Updated all 11 `create_app(...)` call sites in `tests/unit/api/` (`conftest.py` fixture +
    10 direct calls across `test_chat.py`, `test_mlflow.py`, `test_runs.py`) with
    `key_validator=lambda: None` so the existing suite doesn't start requiring real API keys.
  - Tests: `tests/unit/api/test_experiments.py` (8 tests), `tests/unit/api/test_files.py`
    (9 tests), `tests/integration/api/test_startup_validation.py` (2 tests: real
    `Settings.validate_required_keys` against a `tmp_path` settings.yaml, missing-key failure
    and all-keys-present success).
  - `docs/api.md`: added both endpoints to the REST table, their own sections (response shape,
    error table), and a new `## Startup` section documenting the lifespan key-validation seam.
- Deviations from plan: None — implemented exactly as specified, including the implementation
  order (models → routers → main.py wiring → existing-suite regression run → new tests →
  integration test → docs).
- Key decisions:
  - Bug caught during test-writing (not called out in the plan): `except ValueError` in
    `files.py::get_file` was originally ordered before `except UnicodeDecodeError`. Since
    `UnicodeDecodeError` is a `ValueError` subclass, a non-UTF-8 file was being reported as
    `400` instead of the required `415`. Fixed by reordering `UnicodeDecodeError` first (with
    an inline comment explaining why the order matters), verified by
    `test_get_file_returns_415_for_non_utf8_content`.
  - The integration test's stub `settings.yaml` contains only the `api_keys` section (per the
    plan's note that `validate_required_keys` only requires that section), keeping the fixture
    minimal and decoupled from the real `config/settings.yaml` schema.
- Dependencies added: None.
