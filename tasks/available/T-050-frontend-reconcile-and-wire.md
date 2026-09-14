---
id: T-050
phase: 6
agent: frontend-agent
depends_on: [T-038, T-049]
status: available
folders: [frontend/]
outputs: [reconciled client.ts/types.ts, functional Sidebar, ActionBar mounted in Layout]
size: L
branch: ~
pr: ~
---

## Reconcile API client with real backend and wire the dashboard end-to-end

**Scope:** `frontend/`. The frontend was built against provisional/guessed
backend contracts and several components are never connected to real data or
to each other. This task closes both gaps in one pass since the wiring work
depends on the types being correct first.

**Delivers:**
- Fix known `client.ts`/`types.ts` mismatches: snake_case response fields,
  `POST /api/mlflow/open` → `GET /api/mlflow/url`, resume payload key
  `humanFeedback` → `feedback`, real SSE event schema (`event` ∈
  `start`/`end` only, no error frame), real WS chat frame shapes, and real
  (non-`PROVISIONAL`) types for T-049's new experiments/file endpoints
- Replace the static `Sidebar` placeholder with a real run list
  (`listRuns`) and a run-creation control (`createRun`)
- Mount `ActionBar` in `Layout` (built and tested, but currently never
  imported or rendered anywhere)
- Propagate the selected `runId` into `PipelineView`, `Chat`,
  `ExperimentsTable`, `FileViewer` so they render real data instead of their
  permanent empty state

**Done when:**
- [ ] no `PROVISIONAL` type markers remain in `types.ts`
- [ ] creating/selecting a run in the UI populates all four panels and shows
      `ActionBar`
- [ ] `npm run lint && npm run build` pass
- [ ] tests written and passing (types per the Testing strategy in `design.md`)
- [ ] `frontend/README.md` updated
