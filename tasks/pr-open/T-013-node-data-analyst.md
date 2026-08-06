---
id: T-013
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-006]
status: pr-open
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [data_analyst node, eda_report.md, notebooks/01_eda.ipynb]
size: M
branch: feature/T-013-node-data-analyst
pr: https://github.com/MarianodelRio/data-science-lab/pull/15
---

## Node: data_analyst (Pipeline Phase 1)

**Scope:** `src/nodes/llm/data_analyst.py` + `config/agents/data_analyst.yaml` + `config/prompts/data_analyst/v1.md`. Subclasses `LLMNode`.

**Delivers:**
- LLM node that runs EDA by generating Python and executing it via `code_executor` (distributions, correlations, missing, imbalance, cardinality, temporal patterns)
- Writes `reports/eda_report.md` and `notebooks/01_eda.ipynb` via `WorkspaceManager`
- Sets `state["eda_report_path"]`
- `model_role: reasoning`

**Done when:**
- [x] with a mocked LLM returning fixed EDA code + narrative, the node writes `reports/eda_report.md` and `notebooks/01_eda.ipynb`
- [x] `state["eda_report_path"]` points to the written report
- [x] generated code is executed through `code_executor` (asserted via mock), not inline
- [x] `config/agents/data_analyst.yaml` and `config/prompts/data_analyst/v1.md` exist and load
- [x] unit test with mocked LLM + mocked code_executor passes, no network
- [x] `docs/agents.md` row for data_analyst added

## Completed

**Implemented exactly per the approved plan:**
- `config/agents/data_analyst.yaml` — `model_role: reasoning`, `prompt_version: v1`, `tools: [code_executor]`, `output_file_pattern: reports/eda_report.md`, `max_tokens: 4096`.
- `config/prompts/data_analyst/v1.md` — system prompt instructing exactly one fenced ```python block + markdown narrative, dataset discovery via `data/raw/` globbing, and the EDA checklist (missing data, target balance, cardinality, correlations, temporal patterns, leakage flags).
- `src/nodes/llm/data_analyst.py` — `DataAnalystNode(LLMNode)`. Overrides `_write_output` to: parse the LLM response into `(code, narrative)` via a single `_CODE_FENCE_RE` match (raises `ValueError` if no fenced block is found), run `code` through `src.tools.code_executor.execute` (never inline `exec`/`eval`), build `reports/eda_report.md` (narrative + captured stdout, plus an "## Execution errors" section on nonzero returncode/timeout), write it via `workspace.write_text`, and write `notebooks/01_eda.ipynb` via `workspace.write_notebook` (markdown cell = narrative, code cell = extracted code). Overrides `_build_output_state` to set `state["eda_report_path"]`. Confirmed `WorkspaceManager.workspace_path` is the real public attribute name (`src/workspace/workspace_manager.py`) — no deviation needed there.
- `docs/agents.md` — appended the `data_analyst` row under the existing (previously header-only) table.
- `tests/unit/nodes/llm/test_data_analyst.py` — 8 tests per the plan: real config/prompt load, report writing, notebook writing, code-executor-not-inline (including a source-text regression guard for `exec(`/`eval(`), state-delta contents, execution-error section on nonzero returncode, timeout note, and missing-fence `ValueError`. Mocks `src.nodes.llm.base.LLMFactory`/`WorkspaceManager` and `src.nodes.llm.data_analyst.execute` (its own import location), matching `test_base.py`'s convention. All pass in isolation and as part of the full suite; no network calls.

**Deviation from the plan — pre-existing tests broke and needed fixing (not optional per the "all verification commands must pass" instruction):**

`data_analyst` is the *first* concrete node ever to land under `src/nodes/llm/`. `src/graph/phases/generic.py`'s `build_phase_subgraph` calls `resolve_node(name)` **eagerly at graph-build time** (`graph.add_node(name, resolve_node(name))`), not lazily at invoke time as some existing test docstrings assumed — so simply constructing `DataAnalystNode()` (via `GraphBuilder().build()`) now requires `Settings.load()` to succeed, which requires all five `${...}`-interpolated env vars in `config/settings.yaml` to be set, and actually *invoking* the compiled graph now makes a real LLM call unless mocked. This broke three previously-passing test files that build/run the real 7-phase graph without ever expecting a real node to exist:

- `tests/integration/phases/test_phase_subgraphs_smoke.py` — added an autouse fixture mocking `src.nodes.llm.base.LLMFactory` (LLM call) and setting fake values for the five env vars (needed separately by `code_executor.execute`'s own `Settings.load()` call for the timeout); switched the hardcoded `/tmp/comp` workspace to `tmp_path`. Per design.md's testing-strategy table, `src/graph/` integration tests are specified as "full phase run, mock LLM" — this fixture is exactly that, previously moot because no real `LLMNode` existed to invoke.
- `tests/unit/graph/test_builder.py` — removed the `test_no_real_node_modules_exist_yet` canary test (its own docstring: "if a future task lands a concrete node ... this is the signal to revisit `NoOpNode`-dependent assumptions here" — this is that signal). Added an autouse fixture setting fake env vars (build-time construction needs `Settings.load()` to succeed, but never invokes, so no LLM mocking is needed) and resetting `LLMFactory._settings`'s class-level cache, mirroring `tests/unit/llm/test_factory.py`'s `reset_factory_cache` fixture.
- `tests/unit/graph/test_checkpointer.py` — its docstring and assertions already referenced `data_analyst` by name (anticipating this), but it does call `.invoke()` for real; added the same fake-env-vars + `LLMFactory` mock as the smoke test, and switched its hardcoded `/tmp/comp` workspace to a `tmp_path` subdirectory.
- `tests/unit/graph/test_node_resolver.py` and `tests/unit/graph/test_phase_generic.py` were checked and needed no changes — they use fake/injected node modules or fake `resolve_node` functions, never the real 7-phase config, so they're unaffected by any real node landing.

No production code outside the approved plan changed; this was purely test-side accommodation of a now-real code path. Flagging for the Orchestrator: any *future* concrete node landing under `src/nodes/llm/` or `src/nodes/compute/` could hit the same class of failure in these same files if a node happens to be scheduled before phase1's `data_analyst` in build order — worth a note in `context/discoveries.md` so the next node task isn't surprised by it (not added here since it's outside this task's `folders:`, and CLAUDE.md scopes `context/discoveries.md` edits to whoever finds a *new* instance of the issue — the Orchestrator may want to log this centrally).

**Verification — all three commands pass clean from the worktree root:**
- `ruff check . && ruff format --check .` — all checks passed, 68 files already formatted.
- `mypy src/` — Success: no issues found in 43 source files.
- `pytest --cov=src --cov-fail-under=70 -x` — 281 passed, 3 warnings (unrelated torchvision import warnings), total coverage 99.03% (well above the 70% floor; `src/nodes/llm/data_analyst.py` itself is 100%).

**Task file location note:** this task file was found at `tasks/available/T-013-node-data-analyst.md` (status: `available`) in the worktree, not `tasks/in-progress/` as expected — the claim commit that moves/relabels it lands on `main` *after* the lock branch is cut (see `scripts/dt-claim.sh`), so the feature branch's copy of the file never observes that move. Left the file at its current path/frontmatter; the Orchestrator's normal `dt-ready`/`dt-done` flow will reconcile status on `main`.
