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
| `POST /api/mlflow/open` | Launch `mlflow ui` subprocess, return URL |

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

**RunSummary shape:** `{run_id, competition_name, workspace_path, status, phase,
current_iteration, best_score, created_at, updated_at}` — all snake_case,
`status ∈ {pending, running, interrupted, completed, failed}`.

_`POST /api/runs/{id}/submit` and `POST /api/mlflow/open` request/response schemas are not
yet finalized — see `src/api/` and `frontend/src/api/types.ts` once those tasks land._

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

## Frontend client

`frontend/src/api/client.ts` is the single point of backend access from the React app —
components never call `fetch`/`EventSource`/`WebSocket` directly. It exposes one typed
method per endpoint above (`listRuns`, `createRun`, `subscribeToRunEvents`, `connectChat`,
`resumeRun`, `submitRun`, `openMlflow`). Base URL comes from `VITE_API_BASE`
(`frontend/.env.example`); in local dev it is left relative and proxied by Vite
(`frontend/vite.config.ts` → `server.proxy['/api']`) to `localhost:8000`.
