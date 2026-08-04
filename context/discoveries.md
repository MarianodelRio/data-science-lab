# Discoveries

Cross-agent alerts. When an agent finds something that affects another module, it writes here.

## Format

```
## OPEN — YYYY-MM-DD [Source agent → Target agent]
[What was found and what action is needed]
Status: open / resolved in T-XXX
```

## OPEN — 2026-08-04 [frontend-agent → pipeline-agent]
T-038 created `docs/api.md` early (human-approved scope adjustment) because it did not
yet exist and T-038's done-when requires a "Frontend client" note there. Content added:
a minimal endpoint reference table copied from `design.md` § UI architecture, plus a
"Frontend client" section describing `frontend/src/api/client.ts`. T-045 (docs skeletons,
owned by pipeline-agent) is expected to (re)create `docs/api.md` as a fuller "endpoint
reference skeleton (REST + SSE + WebSocket)". When T-045 runs, reconcile rather than
overwrite: merge the existing "Endpoints" table and preserve the "Frontend client"
section from T-038's version into the new skeleton.
Status: open

## OPEN — 2026-08-04 [frontend-agent → api-agent]
`frontend/src/api/types.ts` (T-038) defines provisional TypeScript interfaces for
Run, PipelineEvent, ChatMessage, and related API response shapes, since `design.md`
only documents endpoint purposes, not JSON schemas. When the backend API tasks
(FastAPI routes under `src/api/`) land, please confirm or correct these shapes against
the real response payloads and update `frontend/src/api/types.ts` accordingly.
Status: open

## OPEN — 2026-08-04 [frontend-agent → infra-agent]
`frontend/` (T-038) requires Node.js `>=20.19.0` (pinned via `package.json` `engines`) —
current majors of Vite (8.x), ESLint (10.x), `@vitejs/plugin-react` (6.x), and Vitest
(4.x) all dropped support for Node 18. The dev machine's default `node`/`npm` on PATH
was Node 16.17.0 (and its `npm` shim was broken besides), so this task installed a
local Node 24 LTS tarball under the scratchpad to run `npm install`/`build`/`lint`/`test`;
nothing was changed system-wide. When `docker/` and `.github/` CI config are set up,
pin the frontend build/test image and any Node-based CI job to Node `>=20.19` (LTS 22/24
recommended) to match `frontend/package.json` engines.
Status: open
