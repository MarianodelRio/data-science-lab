---
id: T-038
phase: 4
agent: frontend-agent
depends_on: []
status: done
folders: ["frontend/"]
outputs: [React+Vite+TS app, layout shell, typed API client, dev proxy]
size: M
branch: feature/T-038-frontend-scaffold
pr: "https://github.com/MarianodelRio/data-science-lab/pull/1"
---

## React scaffold + layout + API client (frontend/)

**Scope:** `frontend/` only. Independent of the Python backend — uses the API contract from `design.md`.

**Delivers:**
- Vite + React + TypeScript app with ESLint + Prettier
- Layout shell: sidebar (runs) + main area with tab slots for Pipeline / Experiments / Files / Chat
- Typed API client (`src/api/client.ts`) covering the endpoints in `design.md` § UI; base URL from env (`VITE_API_BASE`)
- Dev proxy to `localhost:8000`; responses mockable for standalone dev
- `npm run build` and `npm run lint` succeed

**Done when:**
- [x] `npm install && npm run build` exits 0
- [x] `npm run lint` exits 0
- [x] the layout renders the four tab regions (component test)
- [x] the API client exposes typed methods for runs/events/chat/submit matching `design.md`
- [x] `.env.example` (frontend) documents `VITE_API_BASE`
- [x] `docs/api.md` "Frontend client" note added

## Completed

**What was implemented:**
- Scaffolded `frontend/` with `npm create vite@latest . -- --template react-ts`
  (Vite 8, React 19, TypeScript ~6.0), then adapted it to the task's stack:
  - Replaced the template's default `oxlint` linter with a flat-config ESLint
    setup (`eslint.config.js`): `typescript-eslint` (recommended) +
    `eslint-plugin-react-hooks` + `eslint-plugin-react-refresh` +
    `eslint-config-prettier` (avoids stylistic clashes with Prettier).
  - Added Prettier (`.prettierrc.json`, `.prettierignore`) and `format` /
    `format:check` npm scripts.
  - Added Vitest + React Testing Library + `@testing-library/jest-dom` +
    `@testing-library/user-event` + jsdom; wired a `test` block into
    `vite.config.ts` (`environment: 'jsdom'`, `setupFiles`) and a `test` npm
    script. `src/test/setup.ts` imports `@testing-library/jest-dom/vitest`
    and registers RTL's `cleanup()` in `afterEach` (project does not use
    Vitest's `globals: true`, so this is done explicitly rather than
    implicitly).
  - Added `server.proxy['/api'] -> http://localhost:8000` (`changeOrigin`,
    `ws: true`) to `vite.config.ts` for local dev.
  - Confirmed `tsconfig.app.json` did **not** default to `strict: true` in
    this Vite template version and added it explicitly.
  - `src/vite-env.d.ts` augments `ImportMetaEnv` with `VITE_API_BASE`;
    `frontend/.env.example` documents it.
  - `src/api/types.ts`: provisional `Run`, `RunStatus`, `PipelineEvent`,
    `ChatMessage`, `CreateRunPayload`, `ResumePayload`, `SubmitResponse`,
    `MlflowOpenResponse` types (explicitly commented as provisional —
    `design.md` documents endpoint purposes, not JSON schemas).
  - `src/api/client.ts`: one typed method per `design.md` § UI architecture
    endpoint — `listRuns`, `createRun`, `subscribeToRunEvents` (SSE, returns
    an unsubscribe fn), `connectChat` (WS, `send`/`onMessage`/`close`, no
    reconnect logic by design), `resumeRun`, `submitRun`, `openMlflow`. REST
    methods and the SSE/WS constructors accept an optional injectable
    `fetch`/`EventSource`/`WebSocket` implementation (default: the global
    one) so call sites are unit-testable without a mocking framework.
  - `src/components/`: `Sidebar` (static "Runs" placeholder, not wired to
    `listRuns()`), four stub panels (`PipelineView`, `ExperimentsTable`,
    `FileViewer`, `Chat` — heading + one placeholder line each), and
    `Layout` (sidebar + main area with an ARIA `tablist`/`tab`/`tabpanel`
    pattern switching between the four stubs; `Pipeline` is the default
    active tab). `App.tsx` now just renders `Layout`; removed the
    template's default counter demo, its CSS/asset cruft
    (`App.css`, `react.svg`, `vite.svg`, `hero.png`, `icons.svg`), and
    rewrote `src/index.css` down to a minimal full-height app-shell reset
    (the template's landing-page-centered layout didn't fit an app shell).
  - `src/components/Layout.test.tsx` (Vitest + RTL): sidebar landmark
    present, tablist with all four named tabs present, default tabpanel
    shows the Pipeline stub, and clicking each other tab switches the
    visible tabpanel content. 4/4 tests pass.
  - `docs/api.md` (new): endpoint reference table (mirrors `design.md` § UI
    architecture) + a "Frontend client" section per the task's scope
    adjustment (human-approved — `docs/api.md` did not exist yet; T-045 owns
    the fuller version and should reconcile rather than overwrite, see
    `context/discoveries.md`).

**What changed vs. the plan / notable adaptations:**
- The installed `create-vite@9.1.2` template (Vite 8 line) defaults to
  `oxlint` instead of ESLint and does **not** default
  `tsconfig.app.json`'s `strict` to `true` — both were explicitly
  overridden per the task's required stack (flat ESLint config, TS strict).
- Local dev environment note (not a design decision, logged for
  `infra-agent` in `context/discoveries.md`): the sandbox's default
  `node`/`npm` on PATH was Node 16.17.0 with a broken `npm` shim, too old
  for current majors of Vite/ESLint/Vitest/`@vitejs/plugin-react` (all
  require Node ≥18–20). Installed a local Node 24 LTS tarball under the
  scratchpad to run `npm install`/`build`/`lint`/`test` for this task only —
  nothing changed system-wide, and `frontend/package.json` now pins
  `"engines": { "node": ">=20.19.0" }` so this is caught explicitly rather
  than silently, wherever this repo is built next (dev machine or CI).

**Decisions and why:** see `context/decisions.md` (2026-08-04, T-038,
6 entries — package manager, test stack, ESLint config format, no MSW,
static Sidebar, `ChatConnection` onError/onClose).

**Verification:** `npm install && npm run lint && npm run build && npm test`
all exit 0 (see PR/commit for full output). `npm run format:check` also
passes.

**Fix pass (post-review, on top of `82c2f25`/`c3bd868`):** the adversarial
reviewer found real gaps; code-quality/security/smoke-tester came back clean.
- `frontend/src/components/Layout.tsx`: the tablist had roving `tabIndex`
  but no `onKeyDown` handler, so keyboard users could reach "Pipeline" via
  Tab but never the other three tabs (`tabIndex=-1` skips them, and Tab
  exits the tablist). Added the standard ARIA tab pattern —
  `ArrowRight`/`ArrowLeft` move focus+selection to the next/previous tab
  (wrapping), `Home`/`End` jump to first/last — via a ref map to each tab
  button. Added two new tests in `Layout.test.tsx` driving this with
  `userEvent.keyboard`.
- `frontend/src/api/client.ts`: `ChatConnection` (from `connectChat`) had no
  error/close signal, asymmetric with the SSE side's `RunEventHandlers.onError`.
  Added `onError`/`onClose` callbacks (no reconnect logic — still explicitly
  out of scope). Also wrapped both SSE and WS `onmessage` `JSON.parse` calls
  in try/catch, routing parse failures through the respective `onError`
  instead of throwing uncaught inside the callback. `onError`'s type is
  `Event | Error` to carry both a native socket/connection error and a
  caught parse error through one callback.
- `frontend/src/api/client.test.ts` (new): 14 tests covering REST happy
  path (URL/method/headers/parsed JSON), the 204→`undefined` case, the
  `!response.ok`→throw case, and SSE/WS wiring (message parsing, malformed
  payload → onError, connection error → onError, WS close → onClose,
  unsubscribe/close actually closing the connection) — all via injected
  `fetchImpl`/`EventSourceImpl`/`WebSocketImpl` stubs, no mocking framework.
- Logged the `ChatConnection` onError/onClose shape as a new decision in
  `context/decisions.md`.
- Full suite now: 20/20 tests passing (6 in `Layout.test.tsx`, 14 in
  `client.test.ts`). `npm run lint`, `npm run build`, `npm test`, and
  `npm run format:check` all green.
