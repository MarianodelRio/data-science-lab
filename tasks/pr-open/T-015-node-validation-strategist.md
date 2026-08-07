---
id: T-015
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: pr-open
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [validation_strategist node, validation/fold_config.json (immutable)]
size: M
branch: feature/T-015-node-validation-strategist
pr: "https://github.com/MarianodelRio/data-science-lab/pull/17"
---

## Node: validation_strategist (Pipeline Phase 1) ⚠️ freezes folds

**Scope:** `validation_strategist` `LLMNode` + agent YAML + prompt. **Enforces the immutable-folds invariant.**

**Delivers:**
- Reads problem definition + EDA; selects CV strategy (stratified/group/time-series/adversarial)
- Generates concrete fold indices and writes `validation/fold_config.json` with `{strategy, n_folds, fold_indices, seed}`
- Sets `state["validation_config_path"]`
- **Write-once guard:** if `fold_config.json` already exists, the node must NOT overwrite it — it raises `FoldsAlreadyFrozenError`
- `model_role: fast`

**Done when:**
- [x] with a mocked LLM the node writes `validation/fold_config.json` containing `strategy` and `fold_indices`
- [x] `state["validation_config_path"]` is set
- [x] calling the node a second time when the file exists raises `FoldsAlreadyFrozenError` and leaves the file byte-identical
- [x] fold_indices cover all rows exactly once (partition check in test)
- [x] agent YAML + prompt v1 exist and load
- [x] unit tests with mocked LLM, no network
- [x] `docs/agents.md` + `docs/pipeline.md` invariant note updated

## Completed

**Implemented per the approved plan (LLM picks CV strategy/params, a generated sklearn script computes real fold indices via `code_executor`, node parses+freezes):**
- `src/nodes/llm/validation_strategist.py` — `ValidationStrategistNode(LLMNode)`. `_build_messages` injects problem definition + EDA report text (read via plain `pathlib.Path`, since `LabState` path fields already hold absolute, previously-validated paths). `_write_output`: checks `validation/fold_config.json` existence *first* (raises `FoldsAlreadyFrozenError` before any write attempt or code execution — second invocation never re-runs the subprocess), extracts the single fenced ```python block, executes it via `code_executor.execute` (never inline `exec`/`eval`), parses stdout as JSON, validates required keys, then validates value shape/content (`_validate_fold_config_shape`): `n_folds`/`seed` must be non-bool ints, `n_folds` positive, `fold_indices` non-empty with `len == n_folds`, each fold's `train`/`val` are non-empty lists of non-negative non-bool ints with no overlap within the same fold. Writes only the 4 approved keys via `workspace.write_json`. `_build_output_state` sets `state["validation_config_path"]`.
- `src/nodes/llm/errors.py` — `FoldsAlreadyFrozenError`.
- `config/agents/validation_strategist.yaml` — `model_role: fast`, `tools: [code_executor]`, `output_file_pattern: "validation/fold_config.json"` (mirrors `data_analyst.yaml`'s shape).
- `config/prompts/validation_strategist/v1.md` — strategy→sklearn mapping (`StratifiedKFold`/`GroupKFold`/`TimeSeriesSplit`; `adversarial` has no native sklearn splitter, documented as `KFold` + an explicit narrative limitation), single-fence + single-line-JSON-stdout output contract, no-exception-swallowing requirement (opposite of `data_analyst`'s guarded style — a fold-computation failure must fail loud, never freeze bad folds).
- `tests/unit/nodes/llm/test_validation_strategist.py` — 26 tests: real config/prompt loading, write success, state delta, extra-key stripping, code-executor-not-inline (+ source-text `exec(`/`eval(` regression guard), missing/multiple fence errors, execution failure/timeout, invalid/missing-key JSON, upstream-context injection (present and missing-path cases), write-once guard with a **real** `WorkspaceManager` (byte-identical file + `execute` called exactly once across two invocations), partition-coverage via a real `sklearn.KFold` split, and malformed-payload rejection (negative index, non-int element, empty train/val, train/val overlap within a fold) — 11 parametrized cases total for the last group.
- `docs/agents.md` — added the `validation_strategist` row. `docs/pipeline.md` — added a `validation_strategist` subsection (mirroring `data_analyst`'s), updated the Phase 1 narrative sentence and the Node classification table, and extended the write-once invariant bullet to name the enforcement mechanism.
- Deviation (necessary, not scope creep, same class of fix T-013 needed when `data_analyst` first landed): `tests/integration/phases/test_phase_subgraphs_smoke.py` and `tests/unit/graph/test_checkpointer.py` share a generic mocked-LLM stdout fixture used to build/invoke the real 7-phase graph; both needed their fixture's stdout changed from a plain string to valid `{strategy, n_folds, seed, fold_indices}` JSON so `validation_strategist`'s stricter stdout-parsing contract doesn't break them now that it resolves to a real node instead of `NoOpNode`. Harmless for every other node (none of them parse stdout).
- Review found and fixed two real gaps across three rounds: (1) code-quality — the write-once payload was checked for required-key presence only, not value shape, before being permanently frozen; fixed with `_validate_fold_config_shape`. (2) smoke-tester — `docs/pipeline.md`'s Phase 1 narrative/node-classification table were stale after round 1's doc edit only touched the invariant bullet; fixed. (3) adversarial — the shape-validation fix from (1) still allowed empty/negative/non-int/overlapping `train`/`val` entries to freeze; tightened with intra-fold type/bounds/disjointness checks (cross-fold coverage verification deliberately left out of the node's runtime checks — the node has no independent way to know the real row count, and is covered by test-time assertions instead).
- Logged a forward-looking `context/discoveries.md` entry (commit `6af3eb8`, already on `main`) for T-016 (`analysis_critic`, not yet implemented): `config/phases/phase1_understanding.yaml` already lists `validation_strategist` as a critic-retry target, but its unconditional write-once guard will raise `FoldsAlreadyFrozenError` uncaught on the very first retry rather than respecting `max_critic_retries` — nothing in `src/graph/` currently catches node-execution exceptions. No code change needed in T-015 itself.
