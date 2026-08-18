---
id: T-028
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: in-progress
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [ensemble_specialist node, stacking/blending design using OOF predictions]
size: S
branch: feature/T-028-node-ensemble-specialist
pr: ~
---

## Node: ensemble_specialist (Pipeline Phase 5)

**Scope:** `ensemble_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs stacking/blending/weighted-average using out-of-fold predictions from prior experiments (leakage-safe)
- Writes `experiments/exp_{next_id}/design.json` referencing the OOF prediction paths of the experiments it combines
- `model_role: reasoning`

**Done when:**
- [x] with a mocked LLM and ≥2 prior experiments in state, the node writes an ensemble `design.json` listing the source OOF paths
- [x] the design uses OOF predictions (not in-fold) — asserted by referenced paths
- [x] agent YAML + prompt v1 exist and load
- [x] unit test with mocked LLM, no network
- [x] `docs/agents.md` row added

## Completed

`ensemble_specialist` is the fifth and last Pipeline Phase 5 specialist. It reads the
solution plan, the frozen fold summary and the out-of-fold (OOF) predictions of prior
experiments, and designs one stacking / blending / weighted-average combiner into
`experiments/exp_{iteration}/design.json`.

**Implemented (inside `folders:`)**

- `src/nodes/llm/ensemble_specialist.py` — `EnsembleSpecialistNode(LLMNode)`, mirroring
  `timeseries_specialist`'s `_build_messages` / `_write_output` override pair and its
  instance-stash mechanism, plus the `base_experiments` machinery:
  `_build_base_experiments`, `_oof_path_for_experiment`, `_experiment_dir_from_entry`,
  `_experiment_id`, `_fallback_iteration`, `_render_base_experiments`.
- `config/agents/ensemble_specialist.yaml` — `model_role: reasoning`,
  `output_file_pattern: experiments/exp_{iteration}/design.json`.
- `config/prompts/ensemble_specialist/v1.md` — prompt v1.
- `src/nodes/llm/_experiment_design.py` — **additive only**: `ENSEMBLE_DESIGN_KEYS`,
  `_validate_base_experiments`, `validate_ensemble_design`. `DESIGN_KEYS` and
  `validate_experiment_design` are byte-for-byte unchanged.

**Decisions and why**

- **`output_file_pattern` uses `{iteration}`, not the task's `exp_{next_id}`** — no id
  allocator exists in the system; T-027 set this precedent.
- **Wrapper over parameter widening.** `validate_ensemble_design` calls
  `validate_experiment_design` unchanged and appends the ninth key, rather than adding a
  keyword-only parameter the four landed siblings would have to keep *not* passing. Keeps
  the frozen `assert tuple(result) == DESIGN_KEYS` test passing untouched.
- **`base_experiments` is node-injected from `state["experiments"]`, never read from the
  LLM response** — same convention as `feature_spec_ref` / `cv_strategy_ref`. All entries,
  in order, never filtered by the LLM. This is what makes the "lists the source OOF paths"
  criterion deterministically assertable.
- **OOF discovery**: each experiment's own `results.json` → `oof_path` when present and it
  re-relativizes cleanly, else the `oof_predictions.parquet` convention in that
  experiment's directory. Binding on `coder` (T-029) — recorded in `context/discoveries.md`.
- **Schema floor is non-empty, not `>= 2`.** The `>= 2` eligibility rule belongs to
  `specialist_selector._should_ensemble`'s routing, not to this schema to re-derive.
- **Alias table deliberately omits bare `weighted` / `weight` / `stack` / `stacked`.**
  `normalize_model_family` has no longest-match-wins rule (T-026 discovery), so only the
  qualified multi-word forms are aliased — this resolves `"weighted blend of stacked
  models"` to `blending` alone. It structurally *cannot* defuse `"blended stacking"`
  (both words are canonical self-match aliases); that is handled at the prompt level with
  explicit rejected examples, mirroring `timeseries_specialist`'s own hybrid case.
- **No `config/phases/*.yaml` registration.** `specialist_selector` already hardcodes
  `"ensemble_specialist"` and dispatches via `resolve_node`; adding it to the phase node
  list would execute it a second time as an unconditional edge.
- **Runtime-unreachable today**, and the module docstring and docs say so plainly: nothing
  writes `state["experiments"]` yet (`coder`/T-029, its only producer, is blocked), so
  `specialist_selector` cannot satisfy its own `>= 2` check in a real run. Same posture as
  the four landed siblings, whose consumer also does not exist yet.

**Also changed** (outside the node's own files, each for a stated reason — the T-024/T-027
precedent; `folders:` deliberately not widened)

- `tests/unit/nodes/llm/test_ensemble_specialist.py` — new; the task's own unit tests.
  `tests/` is outside `folders:` for every node task by construction.
- `tests/unit/nodes/llm/test_experiment_design.py` — tests for the three additions, plus a
  `DESIGN_KEYS` length/order regression tripwire guarding the backward-compat requirement.
- `tests/unit/nodes/compute/test_specialist_selector.py` — **exactly one** added test,
  `test_real_resolve_node_resolves_landed_ensemble_specialist`. The NoOp re-pointing chain
  was terminated by T-027's OPEN discovery and was deliberately not touched.
- `docs/agents.md` — the row required by the Done-when checklist.
- `docs/pipeline.md` — the Phase 5 node bullet (before `code_critic`), the node
  classification table row, the `design.json` contract section (now noting the ninth key),
  and the stale `specialist_selector` paragraph claiming `ensemble_specialist` still falls
  back to `NoOpNode`.
- `context/decisions.md` — the four non-obvious decisions above.
- `context/discoveries.md` — two entries: the sharper `current_iteration` self-overwrite
  hazard, and the OOF-path convention binding on T-029.

**Verification**

`pytest` 1328 passed, global coverage 95%; `ruff check` + `ruff format --check` clean;
`mypy src/` clean. `tests/tools/test_rag.py` (13 tests) fails identically on clean `main` —
`sentence_transformers` is not installed in this environment — and is unrelated to T-028.
