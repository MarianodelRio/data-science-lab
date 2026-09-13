# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend enabling type-aware lint rules by installing `oxlint-tsgolint` and editing `.oxlintrc.json`:

```json
{
  "$schema": "./node_modules/oxlint/configuration_schema.json",
  "plugins": ["react", "typescript", "oxc"],
  "options": {
    "typeAware": true
  },
  "rules": {
    "react/rules-of-hooks": "error",
    "react/only-export-components": ["warn", { "allowConstantExport": true }]
  }
}
```

See the [Oxlint rules documentation](https://oxc.rs/docs/guide/usage/linter/rules) for the full list of rules and categories.

## Pipeline view: connection & terminal-state detection

`PipelineView` (`src/components/PipelineView.tsx`) subscribes to
`GET /api/runs/{id}/events` via `subscribeToRunEvents` (`src/api/client.ts`), which wraps a
native `EventSource`. Native `EventSource` retries a dropped connection on its own, but it
cannot tell a transient network blip apart from the stream ending because the run reached a
terminal state — the server closes the stream itself once a run is `completed`, `interrupted`,
or `failed` (see `docs/api.md` § SSE), and that also surfaces as an `onerror`.

So whenever the SSE connection reports an error, `PipelineView` calls `GET /api/runs/{id}`
(`getRun`) to disambiguate why the stream ended, and branches on the run's `status`:

- **`interrupted`** — switches to an "awaiting human input" display state that points the user
  at the Chat tab. This is display-only: `PipelineView` never calls `resumeRun` or renders
  approve/redirect controls — that belongs to the WebSocket chat contract owned by a later task.
- **`completed` / `failed`** — shows a final state and explicitly calls the unsubscribe function
  returned by `subscribeToRunEvents` to close the `EventSource`, so its native auto-retry
  doesn't reconnect-loop forever against a run that has already finished.
- **anything else** (`pending`/`running`, i.e. a genuine transient drop) or a rejected `getRun`
  call (network failure) — shows a "reconnecting" state and leaves the subscription open,
  relying on the platform's native `EventSource` retry rather than custom backoff logic.

A malformed SSE message (a JSON parse failure) is reported through the same `onError` callback
as a real connection drop, but as a JS `Error` instead of a DOM `Event` — `PipelineView` only
runs the `getRun` disambiguation above for a genuine `Event`; an `Error` is ignored, since the
stream itself is still alive.

## Experiments table: presentational, fixture-driven (unwired)

`ExperimentsTable` (`src/components/ExperimentsTable.tsx`) takes two optional props —
`experiments?: Experiment[]` and `baselineScore?: number | null` — and has no fetch, API
client function, or SSE/WebSocket subscription of its own. There is currently no backend
endpoint that returns an experiments list or a baseline score (`GET /api/runs/{id}` only
returns a scalar `best_score`), so `Layout.tsx` renders `<ExperimentsTable />` with no
props and the component always shows its "No experiments yet." empty state in the running
app today.

The component itself is fully built and tested against fixture data
(`ExperimentsTable.test.tsx`): it renders one row per experiment plus a pinned baseline
row, marks the highest-`cv_score` experiment (ties broken by lowest `iteration`) as best,
computes `delta = cv_score - baselineScore` with an explicit sign, renders `—` for the
delta and a "No baseline yet" baseline row when `baselineScore` is `null`/`undefined`, and
supports sorting by score and by iteration.

See `context/discoveries/T-040.md` for the proposed `GET /api/runs/{run_id}/experiments`
endpoint that would wire this component up for real.

## Chat: WebSocket protocol, reconnect, and history ownership

`Chat` (`src/components/Chat.tsx`) opens a bidirectional connection to
`WS /api/runs/{id}/chat` via `connectChat` (`src/api/client.ts`) whenever it
receives a `runId` prop; with no `runId` it renders a "No run selected."
empty state and never opens a socket (mirrors `PipelineView`'s pattern —
see `Layout.tsx`, which still renders `<Chat />` propless for now).

### Frame protocol

Client → server (`ChatClientFrame`, `src/api/types.ts`):
- `{"type": "question", "text": "..."}` — ask the explainer a question.
  Blank/whitespace-only input is rejected client-side (send is disabled)
  before it would be rejected server-side.
- `{"type": "approve", "feedback": "..."}` / `{"type": "redirect", "feedback": "..."}`
  — resolve the current checkpoint. Both call the same underlying server
  operation (`POST /api/runs/{id}/resume` equivalent) — `redirect` only
  differs in the `feedback` text, neither re-runs the completed phase.
  Chat never calls the REST `resumeRun` directly — calling both would risk
  a double resume.

Server → client (`ChatServerFrame`):
- `{"type": "checkpoint", "phase": "...", "summary": "..."}` — sent once,
  automatically, right after connecting, only if the run is currently
  interrupted; re-sent on every reconnect while still interrupted.
  `phase`/`summary` can legitimately be `""` — that is not a "no
  checkpoint" sentinel, so the approve/redirect controls are gated on
  *receiving this frame at all*, never on its fields being non-empty.
- `{"type": "answer", "text": "..."}` — the explainer's full answer,
  delivered as one complete frame. The backend does not token-stream (the
  explainer call runs via `asyncio.to_thread`); there is nothing to render
  incrementally.
- `{"type": "resumed", "run_id": "...", "status": "running"}` — confirms a
  pending approve/redirect was accepted; clears the pending state on those
  buttons and dismisses the checkpoint controls until the next
  `checkpoint` frame.
- `{"type": "error", "detail": "..."}` — malformed frame, unknown type, or
  a rejected approve/redirect. The socket stays open after this **except**
  for an unknown `run_id`, which triggers exactly one `error` frame and
  then a server-side close.

### Reconnect behavior

`connectChat` performs no reconnect logic itself. `Chat` reconnects by
calling `connectChat` again on an unintentional socket close, with a fixed
backoff schedule (1s, 2s, 4s, 8s, 10s) capped at 5 attempts — a 6th failed
connection (e.g. against an unknown `run_id`, which the server closes
after one `error` frame) stops retrying and shows a terminal
"Unable to reconnect" state rather than hammering a dead endpoint forever.
A deliberate close (switching `runId`, or unmounting) does not trigger a
reconnect.

### History ownership

Conversation history lives only in the WS connection's server-side memory
for that connection's lifetime — it is never persisted to `LabState` or
the workspace. The frontend is the sole owner of chat history across
reconnects: `Chat` keeps its message list in component state and only
clears it when `runId` itself changes (switching to a different run), not
on a same-run reconnect. Repeated `checkpoint` frames for the same
`{phase, summary}` (re-announced on reconnect while still interrupted) are
deduplicated rather than appended twice.
