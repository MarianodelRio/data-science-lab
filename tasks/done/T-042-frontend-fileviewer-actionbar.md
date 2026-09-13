---
id: T-042
phase: 4
agent: frontend-agent
depends_on: [T-038, T-037]
status: done
folders: ["frontend/"]
outputs: [FileViewer component, ActionBar component]
size: S
branch: ~
pr: https://github.com/MarianodelRio/data-science-lab/pull/47
---

## FileViewer + ActionBar components (frontend/)

**Scope:** `frontend/src/components/FileViewer/` + `frontend/src/components/ActionBar/`.

**Delivers:**
- `FileViewer`: markdown renderer for `eda_report.md` / `final_report.md` and a JSON viewer for `feature_importance` / results
- `ActionBar`: "Submit to Kaggle" button (`POST /submit`) and "Open MLflow" button (`GET /api/mlflow/url` → new tab)

**Done when:**
- [ ] FileViewer renders a markdown fixture as formatted HTML
- [ ] FileViewer renders a JSON fixture in a readable tree/table
- [ ] the Submit button calls `/submit` and shows the returned public score (mocked)
- [ ] the MLflow button opens the URL from `/api/mlflow/url` in a new tab
- [ ] `npm run lint` + component tests pass
- [ ] `docs/api.md` note updated

## Completed

- What was implemented (summary of deliverables):
  - Commit 1 (`fix(frontend): reconcile submitRun/openMlflow against T-037
    backend contract`): reconciled `SubmitResponse`
    (`public_score: number | null`, `submission_file`,
    `message: string | null`) and `MlflowOpenResponse`'s endpoint
    (`GET /api/mlflow/url`, was `POST /api/mlflow/open`) in
    `frontend/src/api/types.ts`/`client.ts` against the real T-037 backend
    (`src/api/models.py::SubmitResponse`/`MlflowUrlResponse`,
    `src/api/routers/kaggle.py`/`mlflow.py`), confirmed field-for-field by
    reading those files. `request<T>()` now attaches `status`/`detail` to
    the thrown error via an additive `ApiError = Error & {status, detail?}`
    type — the existing `Error` type and `Request to ... failed with
    status ...` message format are untouched, so no prior call site broke.
    `client.test.ts` covers the new `openMlflow`/`submitRun` shapes plus
    three new cases for the error-detail extraction (present, absent,
    non-JSON body). Updated the `types.ts` PROVISIONAL header list.
  - A small follow-up formatting-only commit (prettier line-wrap on
    `client.ts`/`client.test.ts`) — no behavior change.
  - Commit 2 (`feat(frontend): implement FileViewer ...`): replaced the
    placeholder with a props-driven `FileViewer` — `format: 'markdown'`
    renders via `react-markdown` (default config, no `rehype-raw`, no
    `dangerouslySetInnerHTML`), `format: 'json'` via a small recursive
    `JsonNode` component (plain `dl`/`ul`, no new dependency), both sharing
    an explicit "No content available." empty state. Added
    `react-markdown@^9` via `npm install` (package.json/package-lock.json
    updated together). `Layout.tsx`'s Files tab, which previously rendered
    `<FileViewer />` propless, now renders
    `<FileViewer format="markdown" content={null} />` — the discriminated
    union prop type no longer allows a propless call — same
    "propless-tab-shows-its-empty-state" pattern already used for
    `ExperimentsTable`. `Layout.test.tsx`'s placeholder-text assertion
    updated to match. `FileViewer.test.tsx` covers markdown heading/body
    rendering, the raw-`<script>`-renders-as-inert-text regression guard,
    nested-object and array JSON rendering, and both formats' empty state.
  - Commit 3 (`feat(frontend): implement ActionBar ...`): fully-wired
    `ActionBar` — "Submit to Kaggle" calls `submitRun`
    (`POST /api/runs/{id}/submit`), rendering the numeric `public_score` on
    success, a distinct "Accepted — not yet scored" state (showing the
    response's `message`) when `public_score === null` (strict equality,
    never `?? 0`/truthy — a real `0` score renders as `0`), and
    `detail ?? message` (prefixed with the HTTP status) on 404/409/503/502
    errors. "Open MLflow" fetches `openMlflow`'s URL once in a mount
    `useEffect` (cancelled-flag guarded against post-unmount `setState`,
    `console.error` on failure) and calls `window.open(url, '_blank',
    'noopener,noreferrer')` synchronously as the click handler's first
    statement (no `await` before it) to avoid popup-blocker rejection.
    Both API calls take an optional injected `fetchImpl` prop
    (`Chat.test.tsx`'s stub-injection precedent). `ActionBar.test.tsx`
    covers all 8 scenarios from the plan (success/null/zero score, all
    four error statuses, disabled-without-runId, MLflow open/disabled-
    until-loaded/mount-failure).
  - Commit 4 (`docs(frontend): document FileViewer and ActionBar in
    README`): added the two README sections as the redirect target for the
    struck `docs/api.md` item; `docs/api.md` and `frontend/Dockerfile`
    were not touched.
  - Full verification (all from `frontend/`): `npx vitest run` — 97/97
    passed across 7 test files; `npm run lint` — clean (one pre-existing
    warning in `Chat.tsx`, unrelated to this task); `npm run build`
    (`tsc -b && vite build`) — clean; `npm run format:check` scoped to
    every file this task touched — clean (the repo-wide `format:check`
    flags several pre-existing, out-of-scope files — see Deviations).

- Deviations from plan:
  - **Local Node.js version.** This machine's default `node`/`npm`
    (`~/.local/bin`) is v16.17.0 with a broken `npm` shim, well under the
    `frontend/` `engines.node: >=20.19.0` requirement and under what
    `vitest@4`/`jsdom@30`/etc. need to run at all. Downloaded a standalone
    Node v24.9.0 tarball into the session scratchpad and prepended it to
    `PATH` for every verification command in this session — no repo files
    changed as a result, this only affected how I ran the existing npm
    scripts locally. Flagging this because the next coder/reviewer session
    on this machine will hit the same thing; not writing a
    `context/discoveries/T-042.md` for it since it's a local-environment
    fact, not a cross-module code issue.
  - **`Layout.tsx` / `Layout.test.tsx` touched, not listed in the plan's
    file list.** Necessary consequence of `FileViewer`'s new discriminated-
    union props (condition 1/plan §3): `Layout.tsx` previously rendered
    `<FileViewer />` propless, which no longer type-checks. Updated the one
    call site to `<FileViewer format="markdown" content={null} />` (still
    within `folders: ["frontend/"]`) and fixed the one `Layout.test.tsx`
    assertion that checked the old placeholder text. No other Layout
    behavior changed.
  - **Repo-wide `npm run format:check` fails on pre-existing files this
    task never touched** (`README.md`'s earlier sections, `Chat.tsx`,
    `Chat.test.tsx`, `ExperimentsTable.tsx`, `ExperimentsTable.test.tsx`,
    `PipelineView.test.tsx` — confirmed via `git stash` that these are
    unrelated to this task's diff, pre-existing 80-column violations
    against the repo's pinned `prettier@3.9.6`). Left those files alone
    rather than reformatting unrelated, already-merged code; every file
    this task added or modified passes `prettier --check` individually.
  - Everything else matches the plan as written; no other deviations.

- Key decisions:
  - `MlflowOpenResponse` keeps its frontend name (does not rename to match
    the backend's `MlflowUrlResponse` Pydantic model name) — per plan
    condition 8, only its JSDoc and the endpoint changed.
  - Error-body `detail` extraction (`extractErrorDetail` in `client.ts`)
    wraps `response.json()` in try/catch and returns `undefined` on any
    failure (non-JSON, empty body, JSON without a string `detail`) rather
    than throwing a second error — confirmed via `grep -rn HTTPException
    src/api/` that every raise site uses FastAPI's default
    `{"detail": ...}` shape and no `src/api/` router installs a custom
    exception handler that would override it.
  - `JsonNode`'s `null`/`undefined` leaves render as the literal text
    `"null"` / `"—"` respectively (plan §3), so a JSON `null` value is
    visually distinguishable from a genuinely absent field.

- Dependencies added:
  - `react-markdown@^9.1.0` (runtime dependency, `frontend/package.json` +
    `frontend/package-lock.json`) — added via `npm install react-markdown@^9`
    per plan §5, for `FileViewer`'s markdown rendering with no
    `dangerouslySetInnerHTML`/raw-HTML support.
