---
id: T-034
phase: 3
agent: api-agent
depends_on: [T-009]
status: blocked
folders: ["src/api/"]
outputs: [FastAPI app, POST/GET /api/runs, POST /api/runs/{id}/resume]
size: M
branch: ~
pr: ~
---

## FastAPI skeleton + run management (src/api/)

**Scope:** `src/api/main.py` + `src/api/routers/runs.py`.

**Delivers:**
- FastAPI app factory + uvicorn entrypoint
- `POST /api/runs` — creates a run (competition, workspace), starts the compiled graph as an asyncio background task with a `run_id` thread, returns `{run_id, status}`
- `GET /api/runs` — lists runs with current phase/status
- `GET /api/runs/{id}` — returns one run's state summary
- `POST /api/runs/{id}/resume` — injects `human_feedback` and resumes from the interrupt
- Graph invocation is injected so tests can substitute a fake graph

**Done when:**
- [ ] `POST /api/runs` returns 201 with `{run_id}` and starts a background task (fake graph)
- [ ] `GET /api/runs/{id}` returns 200 with `phase` and `current_iteration`
- [ ] `POST /api/runs/{id}/resume` with `{"feedback":"proceed"}` returns 200 and passes feedback into the graph (asserted via fake)
- [ ] unknown run id returns 404
- [ ] tests use FastAPI `TestClient` + fake graph, no network
- [ ] `docs/api.md` documents these endpoints
