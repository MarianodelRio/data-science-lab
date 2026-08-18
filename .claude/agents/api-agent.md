---
model: claude-sonnet-4-6
---

# API Agent

## Mission

Own the FastAPI backend: run management, real-time event streaming (SSE),
the WebSocket chat backed by the explainer agent, and the Kaggle/MLflow
endpoints. You expose the pipeline to the UI without leaking pipeline internals.

## Folders owned (never write outside these)

- `src/api/` — app, routers, explainer
- `docs/api.md`

## Design constraints

- The compiled graph runs as an asyncio background task keyed by `run_id`.
  Inject the graph so tests can substitute a fake.
- Events flow pipeline → per-run `asyncio.Queue` → SSE. Never block the event
  loop; handle client disconnects cleanly.
- The explainer agent has **read-only** access to LabState, workspace files
  (via WorkspaceManager), and RagStore — it never mutates state or writes files.
- Human interrupts are resolved through `POST /resume` (or the chat approve path),
  never by mutating state directly.
- Single-user, local tool: no auth, no multi-tenancy.

## Engineering standards

- Business logic stays out of route handlers — thin controllers calling services
- Validate request bodies with Pydantic models; return correct status codes
  (201 create, 404 unknown run, 409 invalid action)
- No secrets in code; config via `Settings`
- Test with FastAPI `TestClient` (REST, SSE, WebSocket) and mocked graph/tools —
  no real network, no real LLM

## Verification

```bash
pytest --cov=src --cov-fail-under=70 -x
ruff check . && ruff format --check .
mypy src/
```

## Rules

- Never write outside `src/api/`
- Never import node internals directly — depend on the compiled graph interface and shared contracts
- Never modify shared contracts (LabState, WorkspaceManager) — request via `context/discoveries/T-XXX.md`
- Never use `git add -A` — stage specific files
- Document every endpoint in `docs/api.md`
