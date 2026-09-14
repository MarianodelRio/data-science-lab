# API Reference

Endpoint reference for the FastAPI backend (`src/api/`). Updated by every task that adds or
changes an endpoint.

## REST

| Endpoint | Purpose |
|---|---|
| `GET /api/runs` | List all runs |
| `POST /api/runs` | Create and start a new run |
| `GET /api/runs/{id}` | One run's state summary |
| `POST /api/runs/{id}/resume` | Submit human_feedback, resume from interrupt |
| `POST /api/runs/{id}/submit` | Trigger Kaggle submission |
| `GET /api/runs/{id}/experiments` | List the run's experiments plus baseline score |
| `GET /api/runs/{id}/files/{path}` | Return a workspace file's text content |
| `GET /api/mlflow/url` | Return the browser-reachable MLflow UI URL |

### POST /api/runs
Create and start a new run. Body: `{competition_name, workspace_path, max_iterations?}`
(`max_iterations` defaults to 10). Returns `201 {run_id, status: "pending"}`.
`409` if another run is currently active (single-active-run per design.md's Concurrency NFR).
`422` on empty `competition_name`/`workspace_path` or non-positive `max_iterations`.

### GET /api/runs
List all runs with current phase/status. Returns `200 [RunSummary, ...]`.

### GET /api/runs/{id}
One run's state summary — registry fields plus live `phase`/`current_iteration`/`best_score`
read from the LangGraph checkpoint. `best_score` is `null` until the first experiment
improves on the initial `-inf`. `404` if `id` is unknown.

### POST /api/runs/{id}/resume
Body: `{feedback}`. Injects `human_feedback` into the paused checkpoint and resumes the
graph as a background task. `404` if `id` is unknown, `409` if the run is not currently
interrupted. Returns `200 {run_id, status: "running"}`.

### POST /api/runs/{id}/submit
Trigger a Kaggle submission of the run's best experiment (`state["best_experiment_path"]`,
resolved via `WorkspaceManager.experiment_dir`). No request body. Both Kaggle calls
(`submit`, `get_score`) run off the event loop via `asyncio.to_thread`.

Returns `200 {public_score: float | null, submission_file: string, message: string | null}`.
`public_score` is `null` when Kaggle has accepted the submission but not yet scored it
(`message` explains this — not an error). `submission_file` is workspace-relative
(`experiments/exp_N/submission.csv`).

- `404` — unknown `run_id`.
- `409` — no best experiment yet (`best_experiment_path` unset); `submission.csv` missing
  from the resolved experiment directory (message names the path checked); invalid Kaggle
  competition slug.
- `503` — Kaggle credentials not configured on the server.
- `502` — any other Kaggle API failure during submit or score lookup.

Note: this reads the live checkpoint's `best_experiment_path`, never
`reports/kaggle_submission.json` (that file is a separate record of the automated Phase 7
submission flow, not this endpoint's input) and never falls back to
`experiments/exp_{current_iteration - 1}/submission.csv` — an absent file is always a 409.

### GET /api/runs/{id}/experiments
The run's `LabState.experiments` list plus baseline score, for the frontend `ExperimentsTable`.
Reads the live checkpoint state (same source as `GET /api/runs/{id}`), not a stored/derived copy.

Returns `200 {experiments: [dict, ...], baseline_score: float | null, best_experiment_path: string}`.
- `experiments` is `LabState.experiments` passed through unchanged — ids are unique by
  construction, so there is no server-side rewriting or dedup. Each dict's shape is whatever
  the pipeline wrote (see `docs/pipeline.md`), including any extra/unexpected keys.
- `baseline_score` is `null` until the run's baseline has actually run (i.e. until
  `baseline_results_path` is set) — never the raw `0.0` `LabState` seeds it with.
- `best_experiment_path` is `""` for a run with no improved experiment yet.
- A corrupted/failed experiment's `cv_score` of `inf`/`nan` serializes as JSON `null`
  (`ser_json_inf_nan="null"`, same mechanism as `RunSummary.best_score`), never the invalid
  `-Infinity`/`NaN` token.
- `404` if `id` is unknown.

### GET /api/runs/{id}/files/{path}
Serve one workspace file's content as `text/plain; charset=utf-8`, for the frontend
`FileViewer`. `path` is relative to the run's workspace root and read via
`WorkspaceManager.read_text` directly — no content-type sniffing by extension.

- `400` — rejected path: empty, absolute, or containing a `..` traversal component.
- `404` — unknown `run_id`; file not found; path resolves to a directory.
- `415` — file content is not valid UTF-8 text.

## Startup
The API validates required provider/Kaggle keys (`Settings.validate_required_keys()`) via a
FastAPI lifespan startup handler. A missing or empty required key aborts startup with a clear
`ConfigError` — the server never comes up half-configured. This check runs only when the ASGI
lifespan actually starts (a real server, or an entered `TestClient(app)` context manager),
never at import time and never merely from calling `create_app(...)`. Tests inject a no-op (or
a real check against a `tmp_path` settings file) via `create_app(key_validator=...)`.

### GET /api/mlflow/url
Return the browser-reachable MLflow UI URL — never `workspace.mlflow_tracking_uri`
(`http://mlflow:5000`, only resolvable inside the Docker compose network). Resolved once at
app construction, no `Settings.load()` call in this endpoint's path: `create_app(mlflow_url=...)`
param → `MLFLOW_PUBLIC_URL` env var → `http://localhost:5000` default.

Returns `200 {url: string}`. No error responses.

**RunSummary shape:** `{run_id, competition_name, workspace_path, status, phase,
current_iteration, best_score, created_at, updated_at}` — all snake_case,
`status ∈ {pending, running, interrupted, completed, failed}`.

## SSE

| Endpoint | Purpose |
|---|---|
| `GET /api/runs/{id}/events` | Stream pipeline events in real time |

### GET /api/runs/{id}/events
Server-Sent Events stream of pipeline execution events for a run — one `data:` frame per
graph node start/end, in emission order. `404` if `id` is unknown. Returns an immediately-
closing empty stream if the run has no live event queue right now (e.g. the run finished and
no client streamed it before the terminal sentinel was consumed) — the durable, complete
record is always `runs/{id}/execution.jsonl`, never this stream.

**Event shape** (snake_case JSON, one per `data:` line):
`{timestamp, run_id, iteration, phase, node, event, duration_ms, output_summary}` — same
field semantics as `execution.jsonl` (docs/pipeline.md § Observability), minus
`tokens_in`/`tokens_out`/`model`.
- `event ∈ {start, end}` only — no `error` frame; a run's failure is observable via
  `GET /api/runs/{id}` → `status: "failed"`, not through this stream.
- `duration_ms`/`output_summary` are `null` on `start`, populated on the matching `end`.

**Delivery semantics:** the queue is bounded (1000 events) and drops the *oldest* queued
event to make room for a new one rather than blocking the pipeline or raising — an explicitly
lossy live view. The stream closes automatically once the run reaches a terminal state
(completed/interrupted/failed). A client that disconnects is detected and the stream is torn
down server-side without error. A `: heartbeat` comment line is sent periodically while idle.
Resuming a run (`POST /api/runs/{id}/resume`) opens a fresh queue — reconnect to this endpoint
after resuming.

## WebSocket

| Endpoint | Purpose |
|---|---|
| `WS /api/runs/{id}/chat` | Bidirectional chat with explainer agent |

### WS /api/runs/{id}/chat
Bidirectional JSON-frame chat with the read-only explainer agent, and the channel through
which a human's approve/redirect decision at an interrupt is forwarded into the pipeline.
`404`-equivalent (error frame + socket close) if `id` is unknown.

**Client → server frames** (JSON text frames):
- `{"type": "question", "text": "..."}` — ask the explainer a question about this run.
- `{"type": "approve", "feedback": "..."}` — approve the current interrupt; `feedback` may
  be `""` ("proceed, no comment").
- `{"type": "redirect", "feedback": "..."}` — same operation as `approve`, differing only in
  the `feedback` text: corrective guidance for the pipeline to read in later phases. It does
  **not** re-run the phase that just completed.

**Server → client frames:**
- `{"type": "checkpoint", "phase": ..., "summary": ...}` — sent automatically, once, right
  after connecting, only if the run is currently interrupted.
- `{"type": "answer", "text": "..."}` — the explainer's answer to a `"question"`.
- `{"type": "resumed", "run_id": ..., "status": "running"}` — an `approve`/`redirect` was
  accepted; same underlying call as `POST /api/runs/{id}/resume`.
- `{"type": "error", "detail": "..."}` — malformed frame, unknown `type`, or a rejected
  `approve`/`redirect` (run not interrupted / another run active). The socket stays open
  after an error frame, except for an unknown `run_id`, which closes the connection.

Conversation history is kept in memory for the lifetime of the WebSocket connection only —
it is never written to `LabState` or persisted. The explainer never calls `update_state` and
never writes to the workspace; see `docs/adr/0002-explainer-is-not-an-llmnode.md`.

## Frontend client

`frontend/src/api/client.ts` is the single point of backend access from the React app —
components never call `fetch`/`EventSource`/`WebSocket` directly. It exposes one typed
method per endpoint above (`listRuns`, `createRun`, `subscribeToRunEvents`, `connectChat`,
`resumeRun`, `submitRun`, `openMlflow`). Base URL comes from `VITE_API_BASE`
(`frontend/.env.example`); in local dev it is left relative and proxied by Vite
(`frontend/vite.config.ts` → `server.proxy['/api']`) to `localhost:8000`.
