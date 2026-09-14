---
id: T-050
phase: 6
agent: frontend-agent
depends_on: [T-038, T-049]
status: done
folders: [frontend/]
outputs: [reconciled client.ts/types.ts, functional Sidebar, ActionBar mounted in Layout]
size: L
branch: feature/T-050-frontend-reconcile-and-wire
pr: https://github.com/MarianodelRio/data-science-lab/pull/51
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

## Completed

- **Commit A (`client.ts`/`types.ts` reconciliation)**: `CreateRunPayload` is
  now `{competition_name, workspace_path, max_iterations?}`; added
  `CreateRunResponse {run_id, status}` and `createRun()` now returns it
  instead of a full `Run` (the POST endpoint never returned one). `ResumePayload`
  is now `{feedback}`; `client.ts::resumeRun` no longer sends the wire bug
  `{humanFeedback}` and returns the new `ResumeResponse {run_id, status}`.
  Added `getExperiments()` (`GET /api/runs/{id}/experiments`) and
  `getFileContent()` (`GET /api/runs/{id}/files/{path}`, bypasses
  `request<T>()` since the response is `text/plain`, mirrors its `ApiError`
  shape on failure). Removed every `PROVISIONAL` marker from `types.ts`.
- **Commit B (Sidebar + selection state)**: replaced the static `Sidebar`
  placeholder with a self-fetching component (`listRuns()` on mount, a
  create-run form posting the corrected snake_case payload). On a successful
  create it refreshes via `listRuns()` rather than rendering a `Run`
  synthesized from the `{run_id, status}`-only POST response, then reports
  the new run id up via `onSelectRun`. `Layout.tsx` now owns
  `selectedRunId` (per the Architect/Planner ruling — `App.tsx` stays a
  7-line wrapper) and threads it into `Sidebar`.
- **Commit C (panel wiring + hooks + ActionBar + docs)**: added
  `useExperiments`/`useFileContent` (`src/hooks/`) as the container-side data
  source for the still-presentational `ExperimentsTable`/`FileViewer`; both
  reset run/path-scoped state via a render-time comparison (mirroring
  `PipelineView.tsx`/`Chat.tsx`'s existing pattern) rather than a synchronous
  `setState` in the effect body. `useFileContent` treats a `404` as "not
  generated yet" (`content=null`, `error=null`), matching `FileViewer`'s
  existing degrade-gracefully convention. `Layout.tsx` now threads
  `selectedRunId` into `PipelineView`/`Chat` directly and plain data from the
  hooks into `ExperimentsTable`/`FileViewer`; mounts `ActionBar`
  unconditionally in a new header (never run-gated, since its one-time
  MLflow fetch would otherwise repeat on every run switch — `ActionBar`
  already had an optional `runId` prop disabling submit with no run
  selected, so no changes to `ActionBar.tsx` itself were needed). The Files
  tab adds a fixed, curated report picker (`CURATED_REPORTS`:
  `reports/eda_report.md`, `reports/final_report.md`,
  `reports/leakage_audit.json`) since no endpoint lists workspace files;
  `best_experiment_path` is never offered as a path (it names a directory,
  which the backend 404s on). `frontend/README.md` documents all of the
  above; `docs/api.md` was not touched (api-agent-owned, already accurate).

**Deviations from plan:** None of substance. Minor adjustments to match the
real source: `ActionBar.tsx` already accepted an optional `runId` prop with
submit disabled when absent, so the "resolved by the Orchestrator" fallback
(adding a new prop) was unnecessary — confirmed by reading the file first,
per the plan's own instruction. `GET /api/runs/{id}/files/{path}` uses
Starlette's `{path:path}` converter, which matches literal `/`-containing
values directly, so `getFileContent`'s `path` argument is passed unencoded
rather than through `encodeURIComponent` (encoding would break route
matching, not fix it) — verified directly against `src/api/routers/files.py`
per the plan's explicit instruction to check this rather than guess.

**Key decisions:** `Layout.test.tsx` mocks the `../api/client` module
wholesale (`vi.mock`, mirroring the existing `PipelineView.test.tsx`/
`Chat.test.tsx` pattern) rather than threading an injectable `fetchImpl`
through `Layout` — none of `Sidebar`/`PipelineView`/`Chat`/`ActionBar` receive
one from `Layout`, each already opens its own connection via `client.ts`
directly, so a wholesale module mock was the only way to keep the existing
"no real network calls in unit tests" invariant once `Sidebar` (self-fetching)
and `ActionBar` (unconditional MLflow fetch on mount) are both live in every
`Layout` render.

**Dependencies added:** None.

## Completed — retry round 1 (adversarial review fixes)

Fixed the 3 bugs the adversarial reviewer found; all non-blocking code-quality
items (`CQ-26c8a281`, `CQ-2c363ddc`) were left as-is per the retry brief.

- **Finding 1 (`Sidebar.tsx`, HIGH):** `handleCreate` previously wrapped
  `createRun()` and the post-create `listRuns()` refresh in one `try` block,
  so a refresh failure after a *successful* create was reported as "Failed to
  create run" — and, since the form was never cleared on that path, a retry
  would silently create a duplicate run. Split the two calls into separate
  `try/catch`es: a `createRun()` failure still shows the error and leaves the
  form populated for retry (unchanged); once `createRun()` resolves, the form
  is cleared and `onSelectRun` is called immediately using `createRun`'s own
  `run_id` (no dependency on the refresh), and a subsequent `listRuns()`
  failure is only logged (`console.error`), never shown as a creation error.
  Added a test exercising exactly this sequence — `createRun` resolves,
  `listRuns` rejects — asserting no alert is rendered and the form is cleared.
- **Finding 2 (`Layout.tsx`, MEDIUM):** `useExperiments`/`useFileContent`
  already computed an `error` field distinguishing genuine failures from the
  legitimate empty/404 states, but `Layout.tsx` never rendered it, so a real
  backend failure looked identical to "nothing here yet." Destructured
  `error` from both hooks and render it as a `<p role="alert">` (matching
  `Sidebar.tsx`'s own existing error-rendering convention) next to
  `ExperimentsTable`/`CuratedReportView` respectively — additive to, not a
  replacement for, the existing empty-state rendering. Added two tests
  (`getExperiments`/`getFileContent` rejecting with a non-404 error) asserting
  a visible `alert` with the backend's error detail.
- **Finding 3 (`ActionBar.tsx`, MEDIUM):** Since this PR mounts `ActionBar`
  unconditionally in `Layout` (previously it was never mounted), its
  `submitState`/`submitResult`/`submitError` now persist across run switches
  with nothing resetting them, so a stale result/error from run A kept
  showing after switching to run B. Applied the same render-time reset
  pattern already used twice in this PR (`useExperiments.ts`/
  `useFileContent.ts`): a `prevRunId` state compared against the `runId` prop
  during render, resetting the three submit-state fields back to idle when it
  changes — avoids a synchronous `setState` inside a `useEffect`
  (`react-hooks/set-state-in-effect`, retro L-007). `mlflowUrl` is
  deliberately left out of the reset (fetched once on mount, doesn't vary per
  run). Added two tests (stale success result, stale error) asserting a
  `runId` change clears each back to idle.

**Verification:** `npx vitest run` — 127/127 passing (up from 122; 5 new
tests). `npx eslint .` — 0 errors (1 pre-existing warning in `Chat.tsx`,
unrelated). `npm run build` (`tsc -b && vite build`) — passes. Host Node
(v16.17.0) is below `package.json`'s `engines` requirement
(`>=20.19.0`), so all three were run inside `node:22-bullseye`; no
`package-lock.json` diff resulted.
