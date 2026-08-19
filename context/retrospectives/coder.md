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
