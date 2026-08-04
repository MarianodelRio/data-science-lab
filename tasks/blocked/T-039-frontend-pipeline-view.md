---
id: T-039
phase: 4
agent: frontend-agent
depends_on: [T-038]
status: blocked
folders: ["frontend/"]
outputs: [PipelineView component consuming SSE]
size: M
branch: ~
pr: ~
---

## PipelineView component (frontend/)

**Scope:** `frontend/src/components/PipelineView/`.

**Delivers:**
- Subscribes to `GET /api/runs/{id}/events` via `EventSource`
- Renders current pipeline phase, active node, and a timeline of completed nodes with durations
- Auto-reconnect on drop; visual "waiting for human input" state at interrupts

**Done when:**
- [ ] given a mocked SSE stream, the timeline appends nodes in order (component test)
- [ ] the current phase/node updates live as events arrive
- [ ] an interrupt event switches the view to the "awaiting input" state
- [ ] connection drop triggers a reconnect attempt
- [ ] `npm run lint` + component tests pass
- [ ] `docs/api.md` SSE consumption note updated
