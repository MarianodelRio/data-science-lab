---
id: T-040
phase: 4
agent: frontend-agent
depends_on: [T-038]
status: done
folders: ["frontend/"]
outputs: [ExperimentsTable component]
size: S
branch: ~
pr: https://github.com/MarianodelRio/data-science-lab/pull/44
---

## ExperimentsTable component (frontend/)

**Scope:** `frontend/src/components/ExperimentsTable/`.

**Delivers:**
- Table of experiments per iteration: model, CV score, delta vs baseline, best highlighted
- Baseline row pinned as the permanent reference
- Sortable by score/iteration

**Done when:**
- [ ] given a fixture list of experiments, the table renders one row per experiment plus the baseline row
- [ ] the best experiment is visually marked
- [ ] delta vs baseline is computed and displayed with correct sign
- [ ] sorting by score reorders rows (component test)
- [ ] `npm run lint` + component tests pass
- [ ] `docs/api.md` experiments payload note updated

## Completed

- What was implemented (per the Architect-adjusted scope, superseding this file's original
  wording above — approved before implementation; see `context/decisions/T-040.md`):
  - `frontend/src/components/ExperimentsTable.tsx` (flat file, replacing the 8-line
    placeholder at this exact path — not the `ExperimentsTable/` directory this file's Scope
    line names, matching the repo-wide flat-file convention for `Layout.tsx`, `Sidebar.tsx`,
    `PipelineView.tsx`, `Chat.tsx`, `FileViewer.tsx`): takes optional
    `experiments?: Experiment[]` and `baselineScore?: number | null` props, no fetch/API
    client/SSE subscription of its own (no backend endpoint exists for this data — see
    discoveries entry below). Renders "No experiments yet." when `experiments` is missing or
    empty, regardless of `baselineScore`. Otherwise renders a `<table>` with a permanently
    pinned baseline row first (never reordered or included in sort/best derivation), followed
    by one row per experiment. Best experiment is derived via `findBestId` (highest `cv_score`,
    ties broken by lowest `iteration`, pure over `experiments` only — unaffected by sort or
    baseline) and marked with a plain-text "Best" cell. Delta vs baseline is
    `cv_score - baselineScore`, rendered with an explicit `+`/`-` sign and 4 decimal places, or
    `—` when `baselineScore` is `null`/`undefined` (the baseline row itself then shows "No
    baseline yet" instead of a score). Sorting by score and by iteration toggles
    ascending/descending on repeated header-button clicks; no default sort order — rows render
    in `experiments` array order until a header is clicked.
  - `frontend/src/api/types.ts`: added the `Experiment` interface (`id`, `path`, `cv_score`,
    `iteration`, `model` — snake_case, mirrors `LabState.experiments`/`src/state.py` verbatim)
    placed after `PipelineEvent` and before `ChatMessage`; extended the file's top
    PROVISIONAL doc-comment to name `Experiment` and note it is unverified against any real
    HTTP response (stronger case than the other provisional types, which at least guess at an
    endpoint contract that exists).
  - `frontend/src/components/ExperimentsTable.test.tsx` (new, 10 tests, inline
    `makeExperiment(overrides)` fixture helper per `PipelineView.test.tsx` convention, tests by
    accessible role): no-data state (no props, and `experiments={[]}`, asserting no
    `table`/`row` role present); fixture rendering (3 experiments + baseline, row count and
    per-row text); best-experiment marking (distinct scores, and a tie broken by lowest
    iteration); delta sign/value (above and below baseline, 4-decimal `+`/`-`); no-baseline
    state (`null` and `undefined`, "No baseline yet" plus dash-delta count); sort by score and
    by iteration (ascending then descending on second click, baseline row asserted pinned at
    index 1 — index 0 is the `<thead>` header row also matched by `getAllByRole('row')`).
  - `frontend/src/components/Layout.test.tsx`: updated the two assertions matching
    `/experiments table is not implemented yet/i` (the default-tabpanel-content test and the
    ArrowRight-navigation test) to `/no experiments yet/i`, matching the new component's actual
    no-data output.
  - `frontend/README.md`: appended an "Experiments table: presentational, fixture-driven
    (unwired)" section (sibling `##` heading after T-039's "Pipeline view…" section, matching
    its style) documenting the component's props contract and that it is inert in the running
    app today since `Layout.tsx` renders it with no props and no endpoint supplies data.
  - `context/discoveries/T-040.md` (new): flags to api-agent that no backend route exposes
    `LabState.experiments`/`LabState.baseline_score`, listing the full current route surface
    and proposing a `GET /api/runs/{run_id}/experiments` endpoint. `Status: open`.

- Deviations from plan: none of substance. One self-correction during test-writing: my first
  draft of the sort tests asserted the baseline row at `rows[0]`, forgetting that
  `screen.getAllByRole('row')` also matches the `<thead>` header row — fixed to assert the
  header at index 0 and the pinned baseline at index 1, sorted experiment rows following.

- Key decisions (already made by the Architect, followed as specified — see
  `context/decisions/T-040.md` for full rationale): flat file over a subdirectory; optional
  props with an explicit no-data state (`Layout.tsx` needed no change since it already renders
  `<ExperimentsTable />` with no props); `Experiment` typed snake_case verbatim against
  `src/state.py` rather than guessing camelCase (avoiding a third T-034/T-035-style
  reconciliation pass); `baselineScore: number | null` with a distinct "no baseline yet" state
  rather than defaulting to 0 (src/state.py seeds `baseline_score=0.0`, indistinguishable
  server-side from "no baseline has run"); higher-is-better `delta`/best derivation with no
  `metricDirection` prop (no field anywhere in `LabState` records metric direction to branch
  on); docs landed in `frontend/README.md`, not `docs/api.md` (folder-ownership: this task's
  `folders:` is `["frontend/"]` only, `docs/api.md` belongs to api-agent).

- Dependencies added: none.

- Environment note: this machine's ambient Node.js (v16.17.0 at `~/.local/bin/node`, v12.22.9
  at `/usr/bin/node`) is below `frontend/package.json`'s `engines` requirement
  (`^20.19.0 || >=22.12.0`) and cannot run the installed jsdom 30/undici 8 toolchain (`vitest
  run` fails under Node 20 too, with `webidl.util.markAsUncloneable is not a function` —
  undici 8.10 itself requires Node `>=22.19.0`). Verification (`npm install`, `npm run lint`,
  `npm test`, `npm run build`) was instead run inside a disposable `node:22-bullseye` Docker
  container (`docker run --rm --user 1000:1000 -v <frontend>:/app -w /app node:22-bullseye
  bash -lc "..."`), matching the host user's uid/gid to avoid root-owned files. No project
  files changed to work around this; the resulting `package-lock.json` diff (a `libc` field
  npm 10.9.8 in the container omits vs. the checked-in lockfile) was reverted before
  committing since it was environment noise, not a real dependency change.

- Verification: `npm run lint` (eslint, clean), `npm test` (vitest — 4 files, 49/49 tests
  passed: 15 `client.test.ts`, 18 `PipelineView.test.tsx`, 10 `ExperimentsTable.test.tsx`, 6
  `Layout.test.tsx`), `npm run build` (`tsc -b && vite build`, succeeded).

### Review-fix round

Adversarial review flagged two issues to fix before PR open, plus one documentation-only
finding. Both code fixes are in `frontend/src/components/ExperimentsTable.tsx`, with test
coverage added in `frontend/src/components/ExperimentsTable.test.tsx`; no other files touched
by this round.

- **ADV-65bf4393 (MEDIUM — delta sign/zero handling):** `formatDelta` previously derived its
  `+`/`-` sign from the raw `delta` (`delta >= 0 ? '+' : ''`), which misrepresented two cases:
  an exact tie (`delta === 0`) rendered as `"+0.0000"` (reads as an improvement, not a tie),
  and a sub-precision negative delta (e.g. `-0.00001`) rendered as `"-0.0000"` via
  `toFixed`'s sign-preserving rounding (reads as a display glitch). Fixed by rounding first
  (`Number(delta.toFixed(4))`) and branching sign display on the *rounded* value: `> 0` gets
  `"+"`, `< 0` gets `"-"`, and `=== 0` gets no sign at all — rendering the neutral `"0.0000"`.
  The existing `—` no-data glyph for a missing baseline is untouched; this only changes the
  computed, non-null delta path. Added two tests: an exact-tie case (`cv_score ===
  baselineScore`) and a sub-precision-negative case (`cv_score = 0.75 - 0.000001`,
  `baselineScore = 0.75`), both asserting `"0.0000"` with no sign and explicitly asserting
  neither `"+0.0000"` nor `"-0.0000"` is present.

- **ADV-b5ef67ba (LOW — missing `aria-sort`):** the sortable Score/Iteration `<th>` elements
  toggled sort state but never exposed it to assistive tech. Added an `ariaSortFor(column)`
  helper deriving `"ascending" | "descending" | "none"` from `sortColumn`/`sortDirection`,
  applied only to the Score and Iteration `<th>` elements (Model, Delta vs Baseline, and Best
  remain without `aria-sort`, as they are not sortable). Added a test clicking both headers in
  sequence and asserting `aria-sort` transitions correctly on each (`none` → `ascending` →
  `descending` for the clicked column, `none` throughout for the other).

- **Finding 2 (LOW, documentation only — no code change):** appended a short addendum to the
  existing `context/discoveries/T-040.md` entry (below its `Status: open` line) noting that
  `experiment.id` is used as the React list key with no uniqueness guarantee from the backend
  contract, and that the proposed `GET /api/runs/{run_id}/experiments` endpoint should
  guarantee `id` uniqueness, or a future consumer should key defensively on a composite (e.g.
  `${id}-${iteration}`) if it can't be.

- Verification (same Docker workaround as the original round, ambient Node still v16/v12):
  `npm run lint` (eslint, clean), `npm test` (vitest — 4 files, 52/52 tests passed, including
  13 in `ExperimentsTable.test.tsx`), `npm run build` (`tsc -b && vite build`, succeeded). No
  incidental `package-lock.json` diff this round.
