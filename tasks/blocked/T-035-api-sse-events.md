---
id: T-035
phase: 3
agent: api-agent
depends_on: [T-034]
status: blocked
folders: ["src/api/"]
outputs: [GET /api/runs/{id}/events SSE stream, asyncio.Queue event emitter]
size: M
branch: ~
pr: ~
---

## SSE event stream + event emitter (src/api/)

**Scope:** `src/api/routers/events.py` + an event-emitter used by the pipeline.

**Delivers:**
- An `EventEmitter` backed by a per-run `asyncio.Queue`; the pipeline pushes events `{phase, node, event, timestamp, summary}`
- `GET /api/runs/{id}/events` — Server-Sent Events endpoint streaming that queue to the browser
- Clean disconnect handling (client gone → stop without error); heartbeat to keep the connection alive

**Done when:**
- [ ] pushing an event to a run's queue results in one SSE `data:` frame delivered to a test client
- [ ] events preserve order
- [ ] client disconnect stops the generator without raising
- [ ] the emitter is safe to call from the pipeline background task
- [ ] tests use `TestClient`/httpx streaming, no network
- [ ] `docs/api.md` documents the SSE event schema
