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
