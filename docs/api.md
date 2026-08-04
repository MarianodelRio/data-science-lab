# API Reference

## Endpoints

| Endpoint | Protocol | Purpose |
|---|---|---|
| `GET /api/runs` | REST | List all runs |
| `POST /api/runs` | REST | Create and start a new run |
| `GET /api/runs/{id}/events` | SSE | Stream pipeline events in real time |
| `WS /api/runs/{id}/chat` | WebSocket | Bidirectional chat with explainer agent |
| `POST /api/runs/{id}/resume` | REST | Submit human_feedback, resume from interrupt |
| `POST /api/runs/{id}/submit` | REST | Trigger Kaggle submission |
| `POST /api/mlflow/open` | REST | Launch `mlflow ui` subprocess, return URL |

_Request/response schemas are not yet finalized — see `src/api/` (backend, not yet implemented) and `frontend/src/api/types.ts` (provisional client-side types) once the API implementation tasks land._

## Frontend client

`frontend/src/api/client.ts` is the single point of backend access from the React app —
components never call `fetch`/`EventSource`/`WebSocket` directly. It exposes one typed
method per endpoint above (`listRuns`, `createRun`, `subscribeToRunEvents`, `connectChat`,
`resumeRun`, `submitRun`, `openMlflow`). Base URL comes from `VITE_API_BASE`
(`frontend/.env.example`); in local dev it is left relative and proxied by Vite
(`frontend/vite.config.ts` → `server.proxy['/api']`) to `localhost:8000`.
