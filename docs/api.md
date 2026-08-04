# API Reference

Endpoint reference for the FastAPI backend (`src/api/`). Updated by every task that adds or
changes an endpoint.

## REST

| Endpoint | Purpose |
|---|---|
| `GET /api/runs` | List all runs |
| `POST /api/runs` | Create and start a new run |
| `POST /api/runs/{id}/resume` | Submit human_feedback, resume from interrupt |
| `POST /api/runs/{id}/submit` | Trigger Kaggle submission |
| `POST /api/mlflow/open` | Launch `mlflow ui` subprocess, return URL |

_Request/response schemas are not yet finalized — see `src/api/` (backend, not yet implemented)
and `frontend/src/api/types.ts` (provisional client-side types) once the API implementation
tasks land._

## SSE

| Endpoint | Purpose |
|---|---|
| `GET /api/runs/{id}/events` | Stream pipeline events in real time |

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
