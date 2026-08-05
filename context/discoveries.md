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
Status: resolved in T-045 — merged during rebase: T-038's endpoint entries were split into
REST / SSE / WebSocket tables per T-045's skeleton headings, and the "Frontend client"
section was preserved unchanged.

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

## OPEN — 2026-08-04 [infra-agent (T-002) → pipeline-agent (T-031 score_evaluator)]
Adversarial review of T-002 flagged that `LabState.best_score`/`last_score` have no documented
polarity convention. `new_state()` defaults `best_score=float("-inf")`, which correctly lets the
first experiment always register as an improvement, but only if downstream comparisons assume
"higher score is better." Kaggle competitions routinely use minimize-oriented metrics (RMSE,
LogLoss, MAE). If `score_evaluator` (Pipeline Phase 6) does a naive `last_score > best_score`
without first normalizing/sign-flipping minimize metrics, `best_score`/`best_experiment_path`
will silently freeze after the first experiment and never update again for those competitions —
a silent violation of invariant #3 (best-score-only-on-improvement) that no test would catch
since `float` accepts either polarity. Action needed: `score_evaluator` must normalize all scores
to "higher is better" before writing `last_score`, or `LabState` needs an explicit polarity
field — decide when T-031 is implemented. Documented in `docs/pipeline.md` § State in the
meantime.
Status: open

## OPEN — 2026-08-04 [infra-agent (T-002) → pipeline-agent (T-009 GraphBuilder)]
Adversarial review of T-002 flagged that only `LabState.messages` has a LangGraph reducer
(`add_messages`); every other field defaults to the `LastValue` channel, which raises
`InvalidUpdateError` if two nodes write the same key in the same super-step. `design.md`'s Phase
2 topology runs `literature_researcher` and `web_researcher` concurrently — the only sanctioned
parallel step in the pipeline (CLAUDE.md invariant #6 / design.md invariant #6, max 2 concurrent
LLM agents). When T-009 wires up the actual graph/supervisor, verify whether these two nodes (or
any future concurrent pair) ever write the same `LabState` key in the same step; if so, that
field needs an explicit reducer (e.g. an accumulator for `experiments`) before it can be safely
written concurrently — this would be a `LabState` contract change requiring the same
explicit-approval process as T-002. Documented in `docs/pipeline.md` § State in the meantime.
Status: open

## OPEN — 2026-08-04 [infra-agent (T-002) → pipeline-agent (any node writing `experiments`)]
Adversarial review of T-002 noted `LabState.experiments: list[dict]` has no nested type — the
entry shape (`{id, path, cv_score, iteration, model}`) lives only in a trailing comment in
`design.md`/`src/state.py`, not enforced by mypy. Low risk today (no code writes to this field
yet), but as ~25 future nodes come to read/write it, key-name drift (`cv_score` vs `score`, `id`
vs `exp_id`) becomes possible with no type-checker guardrail. Consider a nested
`ExperimentEntry(TypedDict)` when the first node that populates `experiments` is implemented
(likely T-020 baseline_runner or T-029 coder) — this would be a `LabState` type change and needs
the same explicit-approval process as T-002, so raise it as a proposed design.md amendment at
that time rather than drifting the type silently.
Status: open

## OPEN — 2026-08-04 [pipeline-agent (T-045) → infra-agent (T-001)]
T-045 created README.md with a "## What is this" + "## Documentation" (doc links) section, since it didn't exist yet when T-045 ran. T-001's Done-when checklist also requires "README updated with setup steps" — expect a whole-file conflict on README.md when both PRs merge (both branches independently create/modify it with non-overlapping intent: project description + doc links vs. setup/install steps). Whoever merges second should reconcile by keeping BOTH sections, not picking one side. Not an architecture issue — routine merge resolution, but flagging so it isn't silently resolved by dropping one section.
Status: resolved in T-045 — merged during rebase: README.md now keeps T-001's project
description, Prerequisites, Setup, Development commands, and Docker/CI sections, plus a
new "Documentation" section (added right after "Architecture") linking to all four docs.

## OPEN — 2026-08-05 [infra-agent (T-004) → future task touching src/llm/ or config/settings.yaml]
`ApiKeysConfig` (`src/config/settings.py`) only has fields for `anthropic`, `deepseek`, `groq`,
`kaggle_username`, `kaggle_key` — there is no `openai` or `gemini` field, because
`config/settings.yaml`'s `api_keys` section only lists keys for providers the current
`models.{role}` assignments actually use. `LLMFactory`'s `openai` and `gemini` provider wrappers
(`src/llm/factory.py`) are implemented and dispatchable (any role's `provider` can be set to
`openai`/`gemini` in `config/settings.yaml`), but they construct `ChatOpenAI`/
`ChatGoogleGenerativeAI` with no `api_key` kwarg at all — they rely entirely on the SDKs' own env
fallback (`OPENAI_API_KEY` / `GOOGLE_API_KEY` read directly from the process environment, bypassing
`Settings`/`${ENV_VAR}` resolution and its "missing var raises `ConfigError` naming the file"
guarantee). This is intentional for T-004 (matches the task's approved design), not a bug — but if
a future task actually routes a role to `openai` or `gemini` in `config/settings.yaml`, either add
`openai`/`gemini` fields to `ApiKeysConfig` and thread them through `_build_openai`/`_build_gemini`
the same way `anthropic`/`deepseek`/`groq` already work, or explicitly document the env-fallback
behavior in `.env.example` and `docs/configuration.md` so it isn't a silent gap when the process
env lacks the var (current behavior: the SDK raises its own error, not `ConfigError`).
Status: open

## OPEN — 2026-08-05 [infra-agent (T-007) → any future task calling kaggle_client.get_score/download]
Adversarial review of T-007 found two latent, low-probability failure modes in
`src/tools/kaggle_client.py`, both left unhandled as accepted low-severity gaps (not blocking
merge):
1. `get_score`'s `max(submissions, key=lambda s: s.date)` will raise a bare, decontextualized
   `TypeError` if any submission in the list has `date=None` (verified against installed
   `kagglesdk`'s `ApiSubmission`: `.date` has no `None`-safe fallback, unlike `.public_score`
   which is always coerced to `str`). Unconfirmed whether the real Kaggle API ever actually
   returns `date=None` for a real submission.
2. `download()` assumes the downloaded archive is always named `{competition}.zip`, but the real
   filename is server-URL-derived (per `kaggle`'s own `competition_download_files`
   implementation) and not guaranteed to be `.zip`. A mismatch produces a bare
   `FileNotFoundError` pointing at an invented path, with the actually-downloaded file silently
   orphaned in `dest_dir`.
If a future task hits either of these in practice (e.g. T-018 competition_analyst, T-033
reviewer+report_writer+kaggle_client node, or T-037 API kaggle+mlflow endpoints), wrap the
relevant call with a clearer error message naming the competition and the actual failure, rather
than letting the bare exception propagate.
Status: open
