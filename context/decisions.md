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
