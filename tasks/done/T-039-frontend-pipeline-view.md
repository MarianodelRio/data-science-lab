---
id: T-039
phase: 4
agent: frontend-agent
depends_on: [T-038]
status: done
folders: ["frontend/"]
outputs: [PipelineView component consuming SSE]
size: M
branch: feature/T-039-frontend-pipeline-view
pr: https://github.com/MarianodelRio/data-science-lab/pull/42
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

## Completed

- What was implemented (per the Architect-adjusted scope, superseding this file's original
  wording above — approved by the Orchestrator/human before implementation):
  - `frontend/src/components/PipelineView.tsx` (flat file, matching T-038's `Layout.tsx`
    convention, not a `PipelineView/` directory): takes `runId?: string`, renders "No run
    selected." when absent, otherwise subscribes to `GET /api/runs/{id}/events` via
    `subscribeToRunEvents` and renders current phase, active node(s) (derived via `useMemo`),
    and an ordered timeline of node start/end entries with durations.
  - Timeline correlation uses a FIFO queue keyed by `${phase}::${iteration}::${node}`
    (`openEntriesRef: Map<string, number[]>`) so a node retried within the same
    `{phase, iteration}` (critic-retry invariant) produces two independently-closed timeline
    entries instead of one entry being overwritten twice.
  - Terminal/interrupt detection: on SSE `onError` with a genuine `Event` (not a JS `Error`
    from a malformed-message parse failure), calls `getRun(runId)` and branches on `status`:
    `interrupted` → "awaiting human input" display state (no `resumeRun` call, no
    approve/redirect controls — that is T-041's WebSocket chat contract); `completed`/`failed`
    → final state, explicitly unsubscribes so native `EventSource` retry doesn't loop against
    a finished run; anything else (or a rejected `getRun` promise) → "reconnecting" state,
    subscription left open for the platform's native retry.
  - `frontend/src/api/types.ts`: `PipelineEvent` and `Run` corrected to the real snake_case
    backend shapes from `docs/api.md` / `src/api/event_emitter.py`
    (`context/discoveries/T-034.md`, `T-035.md`); the file's provisional doc-comment now
    names only the types still unreconciled (`ChatMessage`, `CreateRunPayload`,
    `ResumePayload`, `SubmitResponse`, `MlflowOpenResponse`).
  - `frontend/src/api/client.ts`: added `getRun(runId, fetchImpl?)` (`GET /api/runs/{id}`),
    following the existing `listRuns`/`createRun` pattern.
  - `frontend/src/api/client.test.ts`: fixed the three fixtures that used the old camelCase
    `Run`/`PipelineEvent` shape (`listRuns`, `createRun`, `subscribeToRunEvents`'s
    "parses incoming messages" test) and added a `getRun` test.
  - `frontend/src/components/Layout.test.tsx`: updated three stale assertions matching
    `/pipeline view is not implemented yet/i` to `/no run selected/i` — the Architect's plan
    said "two assertions" but the file actually had three matching that string (one in each
    of "shows the Pipeline panel by default", the ArrowLeft-wrap case, and the Home-jump case);
    fixed all three since leaving the third stale would fail the suite.
  - `frontend/README.md`: appended a "Pipeline view: connection & terminal-state detection"
    section documenting the `getRun`-based disambiguation mechanism.
  - `frontend/src/components/PipelineView.test.tsx` (new): 15 tests covering no-run,
    connecting, live timeline updates, FIFO retry correlation, an orphan `end` with no prior
    `start`, interrupted/completed/failed/pending/running `getRun` outcomes, a malformed-frame
    `Error` being ignored, a rejected `getRun` falling back to reconnecting, unmount cleanup,
    and a `runId` change resubscribing with a reset timeline.

- Deviations from plan:
  - The plan's initial component sketch called `setConnectionState('no_run')` /
    `setConnectionState('connecting')` synchronously as the first statements inside the
    subscription `useEffect`. `eslint-plugin-react-hooks`'s `set-state-in-effect` rule
    flagged this (deriving state that render can compute directly, rather than an effect
    reacting to an external event). Fixed by moving the `runId`-change reset (connection
    state, timeline, current phase) to a render-time adjustment using React's documented
    "adjusting state when a prop changes" pattern (comparing `runId` against a `prevRunId`
    state variable), leaving the effect itself to only call `setState` from within
    `onEvent`/`onError`/`checkRunStatus` — i.e. in response to genuine external events. Same
    behavior observable in tests, cleaner lint compliance.
  - `Layout.test.tsx` had three stale assertions referencing the removed placeholder copy,
    not two as the plan described (see above) — updated all three.
  - This project's local Node.js (v16 in one `PATH` entry, v12 in another) is below the
    `frontend/package.json` `engines` requirement (`>=20.19.0`) and cannot run the installed
    Vite 8/Vitest 4/rolldown toolchain (`node:util`'s `styleText` export is missing pre-v20,
    and `npm install` under Node 16 resolves the wrong `@rolldown/binding-*` optional
    dependency for this platform). Verification (`npm install`, `eslint`, `tsc -b`,
    `vitest run`, `prettier --check`) was run instead with the Node v24 binary bundled under
    the local Playwright Python package
    (`~/.pyenv/versions/3.10.12/lib/python3.10/site-packages/playwright/driver/node`) invoked
    directly against each tool's entry script — no project files changed to work around this,
    it is a local-machine environment gap, not a code issue.

- Key decisions:
  - Timeline correlation, `Event`-vs-`Error` disambiguation in `onError`, and the stale-
    `getRun`-resolution guard (`pendingCheckIdRef` + monotonic check ids, invalidated by any
    real `onEvent`) are implemented exactly as specified in the plan's design decisions 1, 3,
    and 4 — see the code comments in `PipelineView.tsx` at each corresponding site.
  - `types.ts`'s edit was kept narrow to `PipelineEvent` and `Run` only, per the approved
    scope — `ChatMessage`, `CreateRunPayload`, `ResumePayload`, `SubmitResponse`, and
    `MlflowOpenResponse` are untouched and still named as unreconciled in the file's top
    doc-comment.
  - Per this task's own steering context (`context-formats.md`, `coder-complete.md`), no
    entries were written directly to `context/decisions/T-039.md` — the Orchestrator prompt's
    step asking for that conflicts with those two steering docs' explicit "Coder does NOT
    write directly to `context/decisions/` during implementation" rule, so this `## Completed`
    section is the sole decision record, as designed.

- Dependencies added: None.

- Review fix-pass (Phase 4 adversarial review):
  - `[ADV-86bef2c6]` (MEDIUM): `activeNodes` was derived purely from `timeline` entries with
    `status === 'running'`, independent of `connectionState`. Once the SSE stream ended on a
    terminal `getRun` resolution (`completed`/`failed`) or the run paused (`interrupted` →
    `awaiting_input`), a node whose matching `end` event was still in flight or had been
    dropped stayed `running` in the timeline forever, so the UI could show a terminal banner
    ("Run completed."/"Run failed.") simultaneously with a non-empty "Active node(s)" line —
    a visible contradiction. Fixed in `frontend/src/components/PipelineView.tsx` by gating
    `activeNodes` on a new `isStreamLive` boolean (`connectionState` is `'live'`,
    `'connecting'`, or `'reconnecting'`): `activeNodes` is now always `[]` outside those three
    states, so `completed`/`failed`/`awaiting_input` can never coexist with a non-empty
    "Active node(s)" line. `awaiting_input` was included in the suppression because the run is
    genuinely paused, not actively running anything, even though the SSE connection itself may
    still be open.
  - `[ADV-686fdce9]` (LOW, same root cause, addressed as cheap polish): a `running` timeline
    entry stranded by the backend's bounded/lossy SSE queue (`docs/api.md` § SSE drops the
    *oldest* queued event under backpressure) now renders as `"{node} — running (unresolved)"`
    instead of a bare `"{node} — running"` once `isStreamLive` is false, so the timeline itself
    signals the entry will never resolve, on top of the "Active node(s)" fix.
  - Tests added to `frontend/src/components/PipelineView.test.tsx` (new describe block
    "PipelineView — active nodes reconciliation", 4 cases): "Active node(s)" clears on both
    `completed` and `failed` despite a stranded `running` entry (and that entry renders with
    the `(unresolved)` suffix); clears on `awaiting_input`; and stays populated (bare
    `— running`, no suffix) while merely `reconnecting`, to prove the fix doesn't over-suppress
    a transient drop that the platform's native retry may still recover from.
  - Not addressed (explicitly out of scope per the review's own note): the `applyEventToTimeline`
    /ref-mutation-inside-`setState`-updater warning (`PipelineView.tsx:46`) and `client.ts`'s
    shared `onmessage` try/catch swallowing real `onEvent` exceptions (`client.ts:94`) — both
    flagged as non-blocking and explicitly "not required for this fix pass."
  - Verification re-run in full after the fix: `npm run lint`, `npm run test` (39 passed,
    including the 4 new cases), `npx tsc -b`, `npm run build` — all green. Same local Node
    version gap as the original implementation pass (ambient `node` on this machine is v16,
    below the `>=20.19.0` engines requirement); this pass instead used a Node v22.14.0 binary
    already staged in the session scratchpad, invoked directly, rather than the Playwright-
    bundled Node the original pass used — no project files changed to work around this.

- Reconciliation note (Orchestrator, Phase 4 rebase): this branch's two commits both appended
  to this file at its old `tasks/in-progress/` path while `main` moved it to `tasks/pr-open/`
  in the interim (via `dt-pr.sh`'s status commit) — a multi-commit instance of the task-file
  rename conflict. Reconciled here via reset-and-squash: `main`'s frontmatter (`status: pr-open`,
  real `pr:` URL) combined with the branch's full accumulated body, replayed as one commit on
  top of current `main`.
