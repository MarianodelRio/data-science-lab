# Decisions

Log of technical decisions made during implementation.

## Format

```
## YYYY-MM-DD — T-XXX [Agent name]
Decided: [what]
Why: [reason]
Affects: [files/modules]
Discarded: [alternative and why not]
```

## 2026-08-04 — T-038 [frontend-agent]
Decided: npm as the package manager for `frontend/` (package-lock.json committed).
Why: the task's own done-when checklist and frontend-agent.md verification commands
hard-code npm; no repo lockfile convention existed yet for any package manager.
Affects: frontend/package-lock.json, all `npm run …` scripts.
Discarded: pnpm/yarn — no prior repo convention to justify the switch.

## 2026-08-04 — T-038 [frontend-agent]
Decided: Vitest + React Testing Library as the frontend test stack.
Why: native Vite integration (shares vite.config.ts), standard for testing by
accessible role rather than implementation detail; design.md's testing strategy
section is Python-only and silent on frontend, so this sets the convention for
future frontend tasks.
Affects: frontend/vite.config.ts (test block), frontend/src/test/setup.ts,
frontend/src/components/Layout.test.tsx, package.json devDependencies.
Discarded: Jest — would need extra config to work with Vite's ESM/TS pipeline
that Vitest gets for free.

## 2026-08-04 — T-038 [frontend-agent]
Decided: Flat ESLint config format (`eslint.config.js`) with typescript-eslint +
eslint-plugin-react-hooks + eslint-plugin-react-refresh + eslint-config-prettier.
Why: current Vite React-TS template default; no prior repo convention to follow.
Affects: frontend/eslint.config.js, package.json devDependencies.
Discarded: legacy `.eslintrc` format — deprecated upstream, not worth adopting new.

## 2026-08-04 — T-038 [frontend-agent]
Decided: No MSW / no runtime mock-data mode in this task.
Why: "mockable for standalone dev" is satisfied via injectable fetch/EventSource/
WebSocket parameters in `frontend/src/api/client.ts` (tests can pass stubs without
a mocking framework), not a runtime mock server — avoids over-building an M-sized
scaffold task.
Affects: frontend/src/api/client.ts.
Discarded: MSW dependency — deferred as a possible future task if standalone-dev
mocking is later needed.

## 2026-08-04 — T-038 [frontend-agent]
Decided: `Sidebar` is left as a static, non-functional placeholder (not wired to
`listRuns()`).
Why: design.md's frontend component table doesn't list a Sidebar/RunList component
and no task explicitly owns wiring it to the API yet; keeps T-038 scoped to "layout
shell, no functionality" as stated in the task's Delivers section.
Affects: frontend/src/components/Sidebar.tsx.
Discarded: wiring it to `listRuns()` now — would pull live-data concerns into a
scaffold-only task.

## 2026-08-04 — T-038 [frontend-agent]
Decided: `ChatConnection` (returned by `connectChat` in `frontend/src/api/client.ts`)
exposes `onError`/`onClose` callbacks, symmetric with the SSE side's `RunEventHandlers.onError`.
Both `onmessage` handlers (SSE and WS) now wrap `JSON.parse` in try/catch and route
parse failures through the relevant `onError` instead of throwing uncaught inside the
event callback.
Why: found during adversarial review — a dropped chat connection or malformed message
was previously silently swallowed with no way for a future `Chat` component to detect
it; `onError`'s type was widened to `Event | Error` so both a native socket error and a
caught parse `Error` can flow through the same callback.
Affects: frontend/src/api/client.ts (`ChatConnection`, `RunEventHandlers`,
`subscribeToRunEvents`, `connectChat`), frontend/src/api/client.test.ts.
Discarded: adding reconnect logic — still explicitly out of scope for this task
(future task's job), `onClose`/`onError` only surface the event.

## 2026-08-04 — T-001 [infra-agent]
Decided: `ruff.toml` sets `extend-exclude = ["*.md"]`.
Why: ruff 0.16 formats fenced Python code blocks inside Markdown files by default. This made
`ruff format --check .` (part of devteam.config.yml's `lint` command) fail on pre-existing
`IDEA.md` and `design.md` — files untouched by this task, containing illustrative code snippets
that are documentation, not source. Every future PR's lint gate would show this same unrelated
failure without an exclude.
Affects: ruff.toml (repo-wide lint config)
Discarded: leaving markdown unformatted-but-failing and asking each future task to reformat docs
it doesn't own — would create noisy unrelated diffs and cross-agent scope violations.

## 2026-08-04 — T-001 [infra-agent]
Decided: root `conftest.py` overrides `pytest_sessionfinish` to convert exit code 5
("no tests collected") into exit code 0.
Why: the task's acceptance criterion is "`pytest` exits 0 (no tests collected is OK)", but
pytest's native exit code for zero collected tests is 5, not 0. Needed for `pip install -e ".[dev]"`
→ `pytest` to be scriptable/CI-friendly during the scaffold-only stage before any tests exist.
Affects: conftest.py (repo-wide pytest behavior) — this will keep masking a genuinely empty
`testpaths` in any future PR too, not just this one; a future task with real tests that
accidentally collects zero tests will still exit 0 instead of failing loudly.
Discarded: leaving exit code 5 as a "failure" — would break the task's own literal acceptance
criterion and block early scaffold/infra PRs that legitimately ship no tests yet.
