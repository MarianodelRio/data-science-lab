# Retrospective Memory — Planner
<!-- max 25 entries; prune lowest-weight (oldest on tie) when exceeded -->
<!-- Weight: 3 = cross-module/architectural, 2 = design/planning, 1 = implementation detail -->

## L-001 | T-032 | 2026-08-19 | Weight: 2
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** Cross-check the plan's own test list against the plan's own implementation instructions — if a specified test cannot pass under the specified design, the plan is internally inconsistent and the Coder will have to deviate.
**Signal:** "The plan's own test `test_non_int_current_iteration_coerces_to_zero` expects the artifact to land at `reports/experiment_plan_0.json` for a boolean `current_iteration`, which `LLMNode`'s default — a raw `state[\"current_iteration\"]` read — does not produce." *(source: ## Completed)*

## L-002 | T-032 | 2026-08-19 | Weight: 2
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** When a ruling is issued for one node's use of a pattern, apply it to every node in the task that uses the same pattern — scope the ruling to the pattern, not to the node that happened to surface it.
**Signal:** "Both stash values on `self` during `_build_messages` for `_write_output` to inject, which is the same hazard the Orchestrator's ruling on Risk 3 addressed for `hypothesis_generator`" *(source: ## Completed)*

## L-003 | T-033 | 2026-08-19 | Weight: 2
**Folders:** src/nodes/llm/, src/nodes/compute/, config/agents/, config/prompts/
**Lesson:** When the plan prescribes a sanitizing helper for one node, prescribe it for every sibling node that handles the same field — and specify at least one test using the field's real production shape, not only the convenient relative one, or the gap survives every gate.
**Signal:** "It now goes through `_delivery_common.safe_relative` (falling back to the well-known path when unusable), making `report_writer` consistent with `reviewer`." *(source: ## Completed)*

## L-004 | T-047 | 2026-08-20 | Weight: 2
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** Do not freeze a values-level choice (a keyword list, a threshold, an enum) by ruling before any evidence exists — rule on the *mechanism* and leave the values open to what review actually measures, or the ruling has to be revoked mid-task.
**Signal:** "the T-047 review fix round changes the keyword floor itself, which the pre-review ruling had frozen." *(source: context/decisions)*

## L-005 | T-047 | 2026-08-20 | Weight: 2
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** When a prompt names the terms a validator enforces, require a test that every named term actually matches — drift in the direction of the prompt over-claiming makes the model trust a guard that is not there.
**Signal:** "`most_frequent_imputer` returned `None` although the prompt promised \"the same three forms for `mean_`, `mode_` and `most_frequent_`\", the exact prompt/validator drift T-022's decision record exists to prevent." *(source: context/decisions)*

## L-006 | T-037 | 2026-09-13 | Weight: 2
**Folders:** src/api/
**Lesson:** When planning to extract a shared private helper out of a router/module into a new file, check `tasks/in-progress/` for other tasks touching the same file or folder — a concurrently in-progress task can independently widen or relocate the same functions before your branch merges, turning what should be a clean extraction into a rebase conflict the Orchestrator has to resolve by hand.
**Signal:** "This conflicted mechanically with the Coder's `src/api/graph_access.py` extraction of the same functions (under the narrower pre-T-036 `Request` typing)." *(source: ## Completed)*

## L-007 | T-039 | 2026-09-13 | Weight: 2
**Folders:** frontend/
**Lesson:** When planning a React component's connection/subscription lifecycle, default to deriving initial/reset state at render time (comparing a prop against its previous value) rather than calling `setState` synchronously at the top of the `useEffect` that owns the subscription — the latter trips `eslint-plugin-react-hooks`'s rule against deriving state an effect could compute directly.
**Signal:** "The plan's initial component sketch called `setConnectionState('no_run')` / `setConnectionState('connecting')` synchronously as the first statements inside the subscription `useEffect`. `eslint-plugin-react-hooks`'s `set-state-in-effect` rule flagged this (deriving state that render can compute directly, rather than an effect reacting to an external event)." *(source: ## Completed)*

## L-008 | T-039 | 2026-09-13 | Weight: 2
**Folders:** frontend/
**Lesson:** When a plan calls for updating stale assertions in an existing shared test file as fallout of a change, grep/count the actual matching assertions rather than estimating — an undercount leaves a failing test in the same PR.
**Signal:** "`Layout.test.tsx` had three stale assertions referencing the removed placeholder copy, not two as the plan described (see above) — updated all three." *(source: ## Completed)*

## L-009 | T-043 | 2026-09-13 | Weight: 2
**Folders:** docker/, ., frontend/
**Lesson:** Do not lock a plan's exact command for probing a third-party Docker image's runtime behavior (e.g. a healthcheck) based on the vendor's own docs or web research alone — flag it as "verify empirically against the actual pinned image tag at implementation time" instead, since a vendor's example can be wrong about what binaries a specific tag actually ships.
**Signal:** "chromadb/chroma:1.5.9 ships neither curl, wget, nor a python interpreter on $PATH (verified empirically via docker exec)." *(source: ## Completed / Deviations from plan)*

## L-010 | T-042 | 2026-09-13 | Weight: 2
**Folders:** frontend/
**Lesson:** When a plan changes a component's prop signature from optional/none to a required shape (e.g. a discriminated union), enumerate every existing call site of that component and add each one to the plan's file list — not just the component file itself — or the Coder discovers the break as an unplanned deviation.
**Signal:** "`Layout.tsx` previously rendered `<FileViewer />` propless, which no longer type-checks." *(source: ## Completed)*
