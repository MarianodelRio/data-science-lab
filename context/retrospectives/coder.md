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
