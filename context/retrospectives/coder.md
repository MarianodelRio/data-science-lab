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

## L-011 | T-035 | 2026-09-12 | Weight: 1
**Folders:** src/api/
**Lesson:** Before writing a streaming-endpoint test against Starlette's `TestClient`, verify the installed version actually supports incremental/partial reads — it may buffer the whole ASGI call before returning, in which case drive the async generator directly (with a minimal fake `Request`) instead of hanging the test suite discovering it live.
**Signal:** "There is no true incremental/partial read available through it, even via `client.stream(...)`." *(source: context/decisions)*

## L-012 | T-036 | 2026-09-13 | Weight: 1
**Folders:** src/api/
**Lesson:** When testing an endpoint whose actual state-mutating work runs inside a background `asyncio.create_task` scheduled before the response is sent, never assert on that work's side effects immediately after receiving the response — poll for it (e.g. a `_wait_until` helper) instead, since the response is sent once the task is registered, not once it completes.
**Signal:** "test_approve_triggers_resume and test_redirect_forwards_feedback_text asserted on fake_graph.update_state_calls[-1] immediately after receiving the "resumed" frame, but graph.update_state(...) runs inside a background asyncio.create_task-scheduled coroutine that is not guaranteed to have completed by then." *(source: ## Completed)*

## L-013 | T-037 | 2026-09-13 | Weight: 1
**Folders:** src/api/
**Lesson:** When a shared dependency's constructor has an established, codebase-wide side effect (e.g. always creating a directory tree), don't try to work around it locally in a new caller — check whether every other call site already accepts the same behavior and whether the side effect is actually a no-op given your caller's real preconditions before treating it as a problem to solve.
**Signal:** "every other `WorkspaceManager` call site in the codebase (all pipeline nodes) accepts the same constructor-creates-root behavior" *(source: ## Completed)*

## L-014 | T-039 | 2026-09-13 | Weight: 1
**Folders:** frontend/
**Lesson:** When correcting a shared, multi-type file that other tasks also depend on (e.g. a provisional `types.ts`), edit only the types your own task actually consumes and leave the rest explicitly marked as still unreconciled — don't opportunistically fix everything in the file just because you're already in there.
**Signal:** "`types.ts`'s edit was kept narrow to `PipelineEvent` and `Run` only, per the approved scope — `ChatMessage`, `CreateRunPayload`, `ResumePayload`, `SubmitResponse`, and `MlflowOpenResponse` are untouched and still named as unreconciled in the file's top doc-comment." *(source: ## Completed)*

## L-015 | T-039 | 2026-09-13 | Weight: 1
**Folders:** frontend/, src/api/
**Lesson:** If an Orchestrator prompt instruction conflicts with this project's own steering docs (`context-formats.md`, `coder-complete.md`), follow the steering docs and say so in `## Completed` rather than silently complying with the conflicting instruction.
**Signal:** "the Orchestrator prompt's step asking for that conflicts with those two steering docs' explicit \"Coder does NOT write directly to `context/decisions/` during implementation\" rule, so this `## Completed` section is the sole decision record, as designed." *(source: ## Completed)*

## L-016 | T-043 | 2026-09-13 | Weight: 1
**Folders:** docker/, ., frontend/
**Lesson:** In an nginx reverse proxy that targets another container by service name, use a `resolver` + variable `proxy_pass` instead of a static `proxy_pass` hostname, so nginx defers DNS resolution to request time rather than failing to start if the upstream container's name isn't yet resolvable when this container boots.
**Signal:** "`frontend/nginx.conf` uses a `resolver 127.0.0.11` + variable `proxy_pass` (`set $upstream_api api:8000; proxy_pass http://$upstream_api$request_uri;`) instead of a static `proxy_pass`, so nginx defers hostname resolution to request time rather than failing to start if `frontend` boots before `api`'s DNS entry exists." *(source: ## Completed / Key decisions)*

## L-017 | T-043 | 2026-09-13 | Weight: 1
**Folders:** docker/, ., frontend/
**Lesson:** For tasks that build large (multi-GB) Docker images, avoid redundant full rebuilds across parallel or resumed agent sessions/reviewers, prune build cache after builds, and prefer a config-only check (e.g. `docker compose config`) over a full rebuild to validate a small post-review fix — repeated heavy rebuilds can exhaust host disk and block all tool use.
**Signal:** "the combined disk usage of this task's Docker builds across several parallel agent attempts (repeated ~8GB `dsl-api` image builds, build cache, and a killed smoke-tester's own build) filled the host's root filesystem to 0 bytes free, blocking all tool use (Bash/Write) for a period." *(source: ## Completed)*

## L-018 | T-040 | 2026-09-13 | Weight: 1
**Folders:** frontend/
**Lesson:** When testing a table's row order or row count with React Testing Library, remember `screen.getAllByRole('row')` also matches the `<thead>` header row, not just `<tbody>` data rows — index data rows starting at 1 (or filter by role scope), not 0.
**Signal:** "my first draft of the sort tests asserted the baseline row at `rows[0]`, forgetting that `screen.getAllByRole('row')` also matches the `<thead>` header row — fixed to assert the header at index 0 and the pinned baseline at index 1, sorted experiment rows following." *(source: ## Completed)*

## L-019 | T-040 | 2026-09-13 | Weight: 1
**Folders:** frontend/
**Lesson:** When rendering a computed numeric delta with an explicit `+`/`-` sign, derive the sign from the rounded/displayed value, not the raw pre-rounding value — an exact tie must render as neutral, not as a false "+" improvement, and a sub-precision negative must not render as a "-0.0000" glitch.
**Signal:** "`formatDelta` previously derived its `+`/`-` sign from the raw `delta` (`delta >= 0 ? '+' : ''`), which misrepresented two cases: an exact tie (`delta === 0`) rendered as `\"+0.0000\"` (reads as an improvement, not a tie), and a sub-precision negative delta (e.g. `-0.00001`) rendered as `\"-0.0000\"` via `toFixed`'s sign-preserving rounding (reads as a display glitch). Fixed by rounding first (`Number(delta.toFixed(4))`) and branching sign display on the *rounded* value." *(source: ## Completed)*

## L-020 | T-040 | 2026-09-13 | Weight: 1
**Folders:** frontend/
**Lesson:** This machine's ambient Node.js is below `frontend/package.json`'s `engines` requirement and cannot run the installed test/build toolchain — run `npm install`/`npm run lint`/`npm test`/`npm run build` inside a disposable `node:22-bullseye` Docker container (matching host uid/gid to avoid root-owned files) instead, and revert any incidental `package-lock.json` diff the container's npm introduces (e.g. a `libc` field) before committing.
**Signal:** "this machine's ambient Node.js (v16.17.0 at `~/.local/bin/node`, v12.22.9 at `/usr/bin/node`) is below `frontend/package.json`'s `engines` requirement (`^20.19.0 || >=22.12.0`) and cannot run the installed jsdom 30/undici 8 toolchain... Verification... was instead run inside a disposable `node:22-bullseye` Docker container... matching the host user's uid/gid to avoid root-owned files." *(source: ## Completed)*

## L-021 | T-041 | 2026-09-13 | Weight: 2
**Folders:** frontend/
**Lesson:** When a `useRef` tracks "the current instance" of an async resource (e.g. a WebSocket connection) and each instance registers its own event listeners via closures, never let a listener unconditionally clear the shared ref — capture the instance's identity at creation time and check `ref.current === thisInstance` before clearing it, because a stale instance's deferred/async event (e.g. `onclose`) can fire after a newer instance already replaced it in the ref, silently destroying the reference to the live one.
**Signal:** "Native `WebSocket.close()` fires `onclose` asynchronously, so the old connection's deferred close (triggered by the effect cleanup on a `runId` change) could arrive *after* the new connection for the new `runId` was already live and referenced by `connectionRef`, nulling out the current, working connection. Result: `connectionState === 'open'` (UI fully enabled) but `connectionRef.current === null`, so `sendFrame` silently dropped every message for the rest of that run's session." *(source: ## Completed)*

## L-022 | T-041 | 2026-09-13 | Weight: 1
**Folders:** frontend/
**Lesson:** When a component's `send`-over-the-wire path needs the underlying connection to actually be open (native `WebSocket.send()` throws `InvalidStateError` before `readyState === OPEN`), don't gate send-enabled UI on "connect was called" — extend the client's connection-object contract with a real open-confirmation callback (`onOpen`) and gate on that instead.
**Signal:** "native `WebSocket.send()` throws `InvalidStateError` before the handshake completes (`readyState !== OPEN`), and unlike `PipelineView`/SSE (receive-only), `Chat` sends over the socket, so it needs a real open confirmation to gate `send`-enabled UI on." *(source: ## Completed)*

## L-023 | T-041 | 2026-09-13 | Weight: 1
**Folders:** frontend/
**Lesson:** Before resetting a `useRef` value directly inside a component's render-time state-reset block (the `if (prop !== prevProp) { ...resets... }` idiom), check this project's actual lint config — `eslint-plugin-react-hooks`'s `react-hooks/refs` rule hard-errors on `ref.current` mutation during render here, contradicting the general "React technically allows mutating a ref not read during this render" caveat. Reset such refs at the top of the effect that owns them instead (an effect keyed on the same prop re-runs exactly once per change and never on an in-effect reconnect/retry).
**Signal:** "`eslint-plugin-react-hooks`'s `react-hooks/refs` rule (already enabled in this project's config) flags direct `ref.current` mutation during render as a hard error (\"Cannot access refs during render\"), not just a style nit — the \"technically allowed by React\" caveat in the task description doesn't hold against this project's actual lint config." *(source: ## Completed)*
