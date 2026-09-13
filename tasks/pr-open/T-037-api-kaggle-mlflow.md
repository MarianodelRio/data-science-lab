---
id: T-037
phase: 3
agent: api-agent
depends_on: [T-034, T-007]
status: pr-open
folders: ["src/api/"]
outputs: [POST /api/runs/{id}/submit, GET /api/mlflow/url]
size: S
branch: feature/T-037-api-kaggle-mlflow
pr: https://github.com/MarianodelRio/data-science-lab/pull/41
---

## Kaggle submit + MLflow URL endpoints (src/api/)

**Scope:** `src/api/routers/kaggle.py` + `src/api/routers/mlflow.py`.

**Delivers:**
- `POST /api/runs/{id}/submit` — triggers a Kaggle submission of the run's best submission via the `kaggle_client` tool; returns `{public_score}`
- `GET /api/mlflow/url` — returns the MLflow tracking UI URL (the Docker `mlflow` service, e.g. `http://localhost:5000`) for the frontend button

**Done when:**
- [x] `POST /api/runs/{id}/submit` calls the tool's `submit`+`get_score` (mocked) and returns `{public_score}`
- [x] submit on a run without a best submission returns 409 with a clear message
- [x] `GET /api/mlflow/url` returns the configured MLflow URL (via `app.state.mlflow_url`/
  `MLFLOW_PUBLIC_URL` — not `Settings.load()`, see Architect ruling #3 in
  `context/decisions/T-037.md`)
- [x] tests use `TestClient` + mocked kaggle tool, no network
- [x] `docs/api.md` documents both endpoints

## Completed

- What was implemented:
  - Extracted `src/api/graph_access.py` (`config`, `live_values`, `get_or_build_graph`,
    `get_or_build_graph_values`) and `src/api/responses.py` (`json_response`) out of
    `src/api/routers/runs.py`'s private helpers, so both `runs.py` and the new
    `kaggle.py` share them without duplication or a cross-router private import.
    `runs.py` now imports these under its old underscore-prefixed names via aliased
    imports — zero behavior change, confirmed by running the pre-existing
    `test_runs.py` suite unmodified before and after.
  - `src/api/routers/kaggle.py` — `POST /api/runs/{id}/submit`: resolves the run's
    `best_experiment_path` from the live graph checkpoint, resolves the experiment
    directory via `WorkspaceManager.experiment_dir`, validates `submission.csv`
    exists, then calls `kaggle_client.submit`/`get_score` (both via
    `asyncio.to_thread`, no `api=` override). Full error mapping per the plan: 404
    unknown run, 409 (no best experiment / missing submission.csv naming the checked
    path / invalid competition slug), 503 missing Kaggle credentials
    (`RuntimeError`), 502 any other Kaggle failure, and 200 with `public_score: null`
    plus an explanatory message when `get_score` raises `TypeError` (worded
    generically per retrospective L-005 — it does not claim the score itself is
    unset, since a second, unrelated `TypeError` cause exists upstream).
  - `src/api/routers/mlflow.py` — `GET /api/mlflow/url` returns
    `app.state.mlflow_url`; imports only `fastapi`/`src.api.models`/
    `src.api.responses`, no `src.config.settings` anywhere in this path.
  - `src/api/main.py` — `create_app` gains `mlflow_url: str | None = None`
    (param → `MLFLOW_PUBLIC_URL` env var → `http://localhost:5000` default),
    registers both new routers.
  - `src/api/models.py` — added `SubmitResponse` and `MlflowUrlResponse`.
  - `tests/unit/api/conftest.py` — `app` fixture now passes a fixed
    `mlflow_url="http://mlflow.example.test:5000"`; tests needing the
    default/env-var resolution paths call `create_app(...)` directly.
  - `tests/unit/api/test_kaggle.py` (11 tests) and `tests/unit/api/test_mlflow.py`
    (5 tests) — all required scenarios from the plan, using real `tmp_path`-backed
    `workspace_path`s so `submission.csv` presence checks hit a real filesystem.
    The concurrency test (`test_submit_uses_asyncio_to_thread_and_does_not_block_event_loop`)
    fires the slow-mocked submit from a background `threading.Thread` against the
    shared `TestClient`/portal and a concurrent `GET /api/runs` from the main thread,
    asserting the concurrent request completes in well under the mocked submit's
    0.3s sleep — this worked directly against the installed `starlette==0.37.2` /
    `httpx==0.28.1` `TestClient` with no adapted mechanism needed (verified per
    retrospective L-011 before trusting it: the test passed on first run and the
    timing assertion has comfortable margin).
  - `docs/api.md` — replaced the stale `POST /api/mlflow/open` row and the "not yet
    finalized" footnote with full `### POST /api/runs/{id}/submit` and
    `### GET /api/mlflow/url` sections (schemas + all status codes).
  - Appended a second dated entry to `context/discoveries/T-037.md` (existing entry
    left untouched) per Architect ruling #8: the router reimplements the thin
    subset of `src/nodes/compute/kaggle_client.py`'s private `_resolve_submission`/
    `_read_leaderboard_score` shape rather than importing them, and a follow-up
    shared-helper promotion is suggested if a third caller ever appears.

- Deviations from plan: None. Implementation follows the Planner's plan and the
  Architect's decision record precisely, including the exact function bodies,
  error-mapping table, and file structure specified.

- Key decisions: None beyond what the Architect's decision record
  (`context/decisions/T-037.md`) already documents — implementation follows those
  rulings as given (see especially rulings #3, #4, #5, #6, #8, referenced inline
  above).

- Dependencies added: None.

**Verification (from the worktree root):**
- `pytest --cov=src --cov-fail-under=70 -x` — 2217 passed, 97.44% total coverage
  (threshold 70%).
- `ruff check . && ruff format --check .` — all checks passed, 162 files already
  formatted.
- `mypy src/` — Success: no issues found in 89 source files.
