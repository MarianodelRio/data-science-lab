# Retrospective Memory — Coder
<!-- max 25 entries; prune lowest-weight (oldest on tie) when exceeded -->
<!-- Weight: 3 = cross-module/architectural, 2 = design/planning, 1 = implementation detail -->

## L-001 | T-032 | 2026-08-19 | Weight: 1
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** When a node needs a path another module already resolved, read the resolved value out of that module's artifact instead of importing its resolver or re-deriving the path yourself.
**Signal:** "The resolved experiment directory is read out of `score_evaluation_{N}.json`'s `experiment_dir`, never re-derived; `src/nodes/compute/_evaluation_common.py` is neither imported nor reimplemented." *(source: ## Completed)*

## L-002 | T-032 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** A reader that degrades instead of raising must leave a machine-readable trace of what it could not read — record each missing input in the artifact you write, or the failure becomes invisible downstream.
**Signal:** "Nothing fails loudly; the only trace is `error_diagnosis_{N}.json`'s `inputs` block being all `null`." *(source: context/discoveries)*

## L-003 | T-033 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, src/nodes/compute/, config/agents/, config/prompts/
**Lesson:** A helper that normalizes a path internally for its own read does not normalize the value you hold — if you also record that value into an artifact, relativize it yourself first, because workspace writes return absolute host paths and the artifact may be published.
**Signal:** "the raw string was the `read_map` key rendered verbatim into" *(source: ## Completed)*

## L-004 | T-033 | 2026-08-19 | Weight: 2
**Folders:** src/nodes/llm/, src/nodes/compute/, config/agents/, config/prompts/
**Lesson:** When a cheap local check can prevent an external call entirely, order it first and pin the ordering with a test asserting zero calls — in a suite where the real SDK is installed and credentials are faked, ordering is the only thing keeping the tests offline.
**Signal:** "The submission file's existence is checked before the first Kaggle API call." *(source: ## Completed)*

## L-005 | T-033 | 2026-08-19 | Weight: 1
**Folders:** src/nodes/llm/, src/nodes/compute/, config/agents/, config/prompts/
**Lesson:** An `except` written for one known cause will silently swallow every other cause raising the same type — before reusing a narrow handler, ask what else raises it, and word the message so it stays honest for all of them.
**Signal:** "`float(latest.public_score)` on a `None` score raises `TypeError`, which the branch written for the T-007 `max(..., key=.date)` hazard swallowed and diagnosed as a `date` problem." *(source: ## Completed)*

## L-006 | T-047 | 2026-08-20 | Weight: 2
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** When you extend a normalization step, match against the old and the new form both — a normalization that only replaces the old one silently drops matches that depended on the previous reading.
**Signal:** "`CatBoost encoding` splits to `cat boost encoding`, which no longer matches the concatenated `catboost` keyword — so the split is added on top of the old reading rather than traded for it." *(source: context/decisions)*

## L-007 | T-029 | 2026-08-31 | Weight: 1
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** When a validated field can legitimately be absent or malformed without that being a hard failure, fall back to the module family's established well-known-filename convention rather than treating it as a validation error — match the degrade style sibling readers already use, don't invent a new one.
**Signal:** "`_oof_artifact_exists` treats a non-string/blank `results.json[\"oof_path\"]` as \"absent\" and falls back to checking the well-known fallback filename, rather than treating it as a hard validation failure — matches the plan's \"falls back... when that path is unset/unusable\" framing used elsewhere in this module family (e.g. `resolve_feature_spec_ref`)." *(source: ## Completed)*

## L-008 | T-029 | 2026-08-31 | Weight: 1
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** A bounded execute-and-retry loop that writes artifacts to a shared directory must clear or isolate each attempt's outputs before the next attempt runs — otherwise a later attempt's validation can silently accept a stale artifact left over from an earlier, different attempt, producing an internally inconsistent result that still passes.
**Signal:** "`_validate_run`'s bare `.exists()` would then silently accept the *stale* `submission.csv` from the earlier attempt alongside the new attempt's fresh `results.json`/OOF, recording an internally inconsistent artifact triplet as a successful run." *(source: ## Completed)*

## L-009 | T-034 | 2026-09-12 | Weight: 1
**Folders:** src/api/
**Lesson:** When a factory function builds an object that wraps an unclosed OS resource (e.g. a raw `sqlite3.connect()`), cache the built object per stable key (e.g. run_id) instead of rebuilding it on every call — rebuilding on every request silently leaks the resource even though every functional test still passes.
**Signal:** "Since the real factory (`_default_graph_factory` -> `GraphBuilder().build` -> `build_checkpointer`) opens a raw, unclosed `sqlite3.connect(...)` per call, this leaked one connection per stored run per `GET /api/runs` poll, unbounded — a real file-descriptor exhaustion path, not a false positive." *(source: ## Completed)*

## L-010 | T-034 | 2026-09-12 | Weight: 1
**Folders:** src/api/
**Lesson:** To close a check-then-act race in an async request handler, keep zero `await` points between the state check and the action that depends on it (e.g. registering a background task) — once a coroutine starts running on the event loop it can't be interleaved until it yields, so removing the yield point removes the race entirely.
**Signal:** "resume_run now builds `graph`/`callback`/`config` synchronously (no `await`), calls `asyncio.create_task(_resume_and_track(...))`, and assigns `request.app.state.active_runs[run_id] = task` on the very next line — zero `await` anywhere between the interrupted-status check and that assignment, closing the race window entirely" *(source: ## Completed)*
