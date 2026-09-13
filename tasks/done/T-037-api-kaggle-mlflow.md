---
id: T-037
phase: 3
agent: api-agent
depends_on: [T-034, T-007]
status: done
folders: ["src/api/"]
outputs: [POST /api/runs/{id}/submit, GET /api/mlflow/url]
size: S
branch: ~
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

- Deviations from plan: One, made by the Orchestrator during the Phase 4 rebase onto
  `main`, not by the Coder. T-036 (merged to main while this branch was in progress)
  independently widened `runs.py`'s `_config`/`_get_or_build_graph`/
  `_get_or_build_graph_values`/`_live_values` to accept `HTTPConnection` (so the chat
  WebSocket handler could reuse them) and kept them local to `runs.py`, with
  `src/api/routers/chat.py` importing them directly
  (`from src.api.routers.runs import _config, _get_or_build_graph, do_resume`). This
  conflicted mechanically with the Coder's `src/api/graph_access.py` extraction of the
  same functions (under the narrower pre-T-036 `Request` typing). Resolved by dropping
  `graph_access.py` entirely and having `src/api/routers/kaggle.py` import
  `_get_or_build_graph_values` directly from `src.api.routers.runs`, matching
  `chat.py`'s existing precedent — one shared implementation, one import pattern,
  `HTTPConnection`-typed (a `Request` is-a `HTTPConnection`, so `kaggle.py`'s `Request`
  argument is still accepted unchanged). `src/api/responses.py` (the `_json` /
  `json_response` extraction) had no such conflict and was kept as originally planned.
  No behavior change to any endpoint; `docs/api.md`'s content is unaffected. Full test/
  lint/type-check suite re-run clean after the rebase (see below).

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

## Completed — 2026-09-13 review-fix pass

Fixed the 2 BLOCKER + 3 WARNING/NITPICK/INFO findings from T-037's adversarial/
code-quality/security review pass (`ADV-7ba87f78`, `ADV-2e66cc85`, `CQ-dd870741`,
`CQ-4c535a46`, `CQ-7a60b5fe`, `SEC-336b6da1`). `CQ-5318acd4` and `SEC-8a1ce12f` were
left as-is per the review's own guidance (no change needed).

- **`[ADV-7ba87f78]`** — added a per-`run_id` in-flight guard for
  `POST /api/runs/{id}/submit`. `app.state.active_submissions: set[str]` is
  initialized in `create_app` (`src/api/main.py`), alongside `active_runs` but kept
  as its own collection (a distinct concept — in-flight submissions, not pipeline
  runs). `submit_run` (`src/api/routers/kaggle.py`) now checks-and-409s
  (`"a submission for this run is already in progress"`) before its first `await`,
  adds `run_id` to the set with zero `await` in between (same zero-`await` discipline
  `resume_run` established for `active_runs`, per retrospective L-010), and the rest
  of the original handler body was extracted into `_do_submit`/`_submit_and_score` so
  it could be wrapped in a `try/finally` that clears the guard on every exit path
  (success, every `HTTPException`, any unexpected exception).
- **`[ADV-2e66cc85]`** — `WorkspaceManager(...)` construction, `.experiment_dir(...)`
  resolution and `submission_path.is_file()` are now bundled into one
  `_resolve_submission_path` helper run via a single `asyncio.to_thread(...)` call,
  matching the handler's existing threading pattern. Read `workspace_manager.py`
  before deciding the boundary: `WorkspaceManager.__init__` does blocking
  `mkdir(parents=True, exist_ok=True)` (real I/O); `.experiment_dir()` itself is pure
  path computation (`_resolve` does no I/O, no `mkdir`) but is bundled in anyway since
  it must run after the constructor and before `.is_file()` in the same thread-hop;
  `.is_file()` is blocking disk I/O. Design call on the mkdir side effect: kept as-is
  rather than avoiding it, because (a) every other `WorkspaceManager` call site in the
  codebase (all pipeline nodes) accepts the same constructor-creates-root behavior,
  (b) `WorkspaceManager`'s public API is a protected contract this task must not
  change without explicit human approval, and (c) in practice `record.workspace_path`
  already exists by the time a run can reach `submit` (it was created at
  `create_run`/`resume_run` time), so the `mkdir(exist_ok=True)` is a no-op in the
  common case — the endpoint's read-only *intent* is preserved in effect even though
  the call itself is not side-effect-free in principle.
- **`[CQ-dd870741]` + `[CQ-4c535a46]`** — added `logger = logging.getLogger(__name__)`
  at module scope (one addition serves both findings). The `OSError`/`ValueError`
  path now `logger.warning`s the original exception (with `run_id`/`experiment_name`)
  before the generic 409; the 503 (missing Kaggle credentials) and both 502 paths
  (submission failure, leaderboard read failure) now log with `run_id` +
  `competition_name` context (`logger.warning` for the expected-configuration-error
  503, `logger.exception` for the two unexpected-failure 502s, to capture a trace).
- **`[CQ-7a60b5fe]`** — added an explicit empty-`experiment_name` guard (same 409 as
  an unset `best_experiment_path`) right after computing
  `Path(best_experiment_path).name`, before building `relative_submission` — prevents
  a malformed `experiments//submission.csv` message.
- **`[SEC-336b6da1]`** — folded into `_resolve_submission_path` (see ADV-2e66cc85
  above): `.is_file()` now runs inside the same function whose `OSError`/`ValueError`
  the caller already catches and maps to the generic 409, so a `ValueError` from
  `Path.is_file()` (e.g. an embedded NUL byte) is no longer uncaught.

Tests added to `tests/unit/api/test_kaggle.py` (all pass):
- `test_submit_returns_409_when_submission_already_in_progress` — pre-populates
  `app.state.active_submissions` to simulate an in-flight request and asserts a 409
  with zero calls to the (monkeypatched) `kaggle_client.submit`.
- `test_submit_clears_in_flight_guard_on_success` /
  `test_submit_clears_in_flight_guard_on_error_exit` — confirm the guard is released
  on both the success path and an early-409 failure path.
- `test_submit_resolves_submission_path_off_event_loop` — extends the existing
  `test_submit_uses_asyncio_to_thread_and_does_not_block_event_loop` pattern with a
  `_SlowWorkspaceManager` subclass (sleeps in `__init__`) monkeypatched in for
  `kaggle_router.WorkspaceManager`, proving a concurrent `GET /api/runs` completes
  well under the sleep while the submit request is resolving the submission path.
- `test_submit_returns_409_when_experiment_name_is_empty` — covers `CQ-7a60b5fe`
  (`best_experiment_path="/"` → `Path("/").name == ""`).
- Left `test_submit_uses_asyncio_to_thread_and_does_not_block_event_loop`
  (`CQ-5318acd4`) unmodified: it exercises a concurrent `GET /api/runs`, not a second
  `submit` for the same `run_id`, so the new `active_submissions` guard does not
  interact with it — confirmed by running it after the change; still passes with the
  same margin.

No new external dependencies. No changes outside `src/api/`; nothing new found for
`context/discoveries/T-037.md`.

**Verification (from the worktree root):**
- `pytest --cov=src --cov-fail-under=70 -x` — 2242 passed, 97.24% total coverage
  (threshold 70%); `src/api/routers/kaggle.py` at 100% line coverage.
- `ruff check . && ruff format --check .` — all checks passed, 165 files formatted
  (kaggle.py reformatted once by `ruff format` after the edits, then verified clean).
- `mypy src/` — Success: no issues found in 90 source files.
