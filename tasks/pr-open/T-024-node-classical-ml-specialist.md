---
id: T-024
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: in-progress
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [classical_ml_specialist node, experiment design with Optuna search space]
size: S
branch: feature/T-024-node-classical-ml-specialist
pr: ~
---

## Node: classical_ml_specialist (Pipeline Phase 5)

**Scope:** `classical_ml_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs an experiment for XGBoost/LightGBM/CatBoost/ExtraTrees: model choice, preprocessing, and an Optuna search space
- Writes the design to `experiments/exp_{next_id}/design.json` (design only — the coder implements it)
- `model_role: reasoning`

**Done when:**
- [x] with a mocked LLM the node writes an experiment `design.json` containing a `search_space` and `model_family`
- [x] the design references the frozen folds (does not redefine CV)
- [x] agent YAML + prompt v1 exist and load
- [x] unit test with mocked LLM, no network
- [x] `docs/agents.md` row added

## Completed

Implemented `ClassicalMlSpecialistNode` (`src/nodes/llm/classical_ml_specialist.py`, `LLMNode`)
plus `config/agents/classical_ml_specialist.yaml` (`model_role: reasoning`, `output_file_pattern:
"experiments/exp_{iteration}/design.json"`, `max_tokens: 4096`) and
`config/prompts/classical_ml_specialist/v1.md`.

The bulk of the task is the new shared module `src/nodes/llm/_experiment_design.py` — the
`design.json` contract for all five Phase-5 specialists (T-024–T-028) and their consumer `coder`
(T-029). It exposes `extract_json_object`, `normalize_model_family`,
`validate_experiment_design`, `read_fold_summary` and `resolve_feature_spec_ref`, all parameterized
by the calling `specialist` name (the `_research_common.py` convention), and declares no class
matching its own filename stem so `node_resolver._find_node_class` never mistakes it for a node.
`validate_experiment_design` is a whitelist rebuild returning exactly `DESIGN_KEYS` in order;
`specialist`, `feature_spec_ref` and `cv_strategy_ref` are node-injected and never read from the
LLM, and every other unknown top-level key (including `n_trials`/`early_stopping_patience`, which
belong to `config/settings.yaml`'s `optuna:` block) is dropped.

Design decisions worth calling out (10 entries in `context/decisions.md`): experiment id is
`state["current_iteration"]` via the inherited `_resolve_output_path`, with no id allocator, no new
`WorkspaceManager` method and no new `LabState` field; the nine forbidden CV keys are rejected
loudly by exact key name at the top level and inside `search_space`/`fixed_params` (so "does not
redefine CV" is assertable rather than vacuous under a whitelist), with `cv_strategy_ref`
deliberately outside that set; `model_family` is normalized to a canonical token by word-boundary
alias matching and an ambiguous two-family answer is rejected rather than resolved by precedence,
since `coder` dispatches on that value; `search_space` must be non-empty and `log: true` may not be
combined with `step`, both failing at design time instead of inside the Optuna subprocess;
`feature_spec_ref` is relativized (a host-absolute path breaks inside `code_executor`'s subprocess
and inside the container) and stashed on the instance in `_build_messages`, since
`LLMNode.__call__` never passes `state` to `_write_output`.

`read_fold_summary` injects only `strategy`/`n_folds`/`seed` — never `fold_indices` — and every
upstream read degrades to a placeholder rather than raising, so Phase 5 stays invokable standalone.

Two discoveries logged: (1) nothing in `src/` ever increments `current_iteration`, so every
iteration-scoped output path (T-021's, T-022's, `competition_analyst`'s and now this one) resolves
to the same file and silently overwrites — pre-existing, for whoever lands the iteration loop; (2)
an expected `docs/pipeline.md`/context-file conflict with PR #25 (T-023), plus the note that
`docs/agents.md`'s new step-3 exception (the 5 specialists are not listed in
`config/phases/phase5_implementation.yaml`) only becomes true once PR #25's YAML trim lands.

Tests: 125 in `tests/unit/nodes/llm/test_experiment_design.py` (pure functions plus real
`tmp_path`-backed `WorkspaceManager` I/O, no mocks) and 27 in
`tests/unit/nodes/llm/test_classical_ml_specialist.py` (LLM and `WorkspaceManager` mocked, no
network). `tests/integration/phases/test_phase_subgraphs_smoke.py` gained a
`_MOCK_CLASSICAL_ML_DESIGN` payload and one dispatch line. Docs updated: a `docs/agents.md` row
plus the step-3 exception note, and a `### Implementation (Phase 5)` section in `docs/pipeline.md`
with the `design.json` contract block and a node-classification row.

Explicitly not modified: `config/phases/phase5_implementation.yaml`, `src/state.py`,
`src/workspace/workspace_manager.py`, `src/config/*`, `config/settings.yaml`, and every sibling
node module.

## Review round (2026-08-12)

Code-quality WARNING, security WARNING, adversarial WARNING, smoke-tester 5/5 PASS — no blockers,
but adversarial proved several defects by execution. Since `_experiment_design.py` is the contract
T-025–T-029 inherit, all eleven were fixed now rather than four tasks from now.

**Correctness / robustness.** `read_fold_summary` caught only `OSError`, so a truncated or empty
`validation/fold_config.json` (`json.JSONDecodeError`), invalid UTF-8 (`UnicodeDecodeError`) or a
path outside the workspace root (`ValueError` from `Path.relative_to`) escaped and killed the run —
reproduced through the real phase-5 subgraph. It now catches `(OSError, ValueError)`, which covers
all three. `resolve_feature_spec_ref` raised on a foreign absolute path (a resumed run whose
workspace moved or is bind-mounted elsewhere) despite documenting "never returns `""`", and passed a
stored `..` traversal straight into `design.json`; both now fall back to the iteration pattern.
`math.isfinite` raises `OverflowError` on a large enough Python int, and `json.loads` raises a bare
`ValueError` on an integer literal past CPython's 4300-digit limit — both are now `ValueError`s
naming the specialist, so the module's single-exception-type contract actually holds.

**Security.** `search_space`/`fixed_params` parameter *names* were entirely unvalidated and become
Python keyword arguments in `coder`-generated code that `code_executor` runs; they now must match
`^[A-Za-z_][A-Za-z0-9_]{0,63}$`. `preprocessing` entries were "any non-empty string", which let
`"StratifiedKFold(n_splits=3, shuffle=True)"` reach disk — a CV redefinition hiding in a list value,
past the forbidden-*key* guard, defeating this task's own acceptance criterion; entries are now
lower_snake tokens (`^[a-z][a-z0-9_]{0,63}$`), a shape constraint rather than a closed vocabulary so
T-025–T-028 keep their own choice of steps. Non-finite floats are rejected everywhere, since
`WorkspaceManager.write_json` uses `json.dump`'s `allow_nan=True` default and would otherwise emit a
`design.json` that fails `JSON.parse` in any non-Python consumer.

**Optuna semantics** (verified against optuna 3.5.0): `step > high - low` is rejected — Optuna does
not complain, it just returns the same value on every trial and burns the budget without tuning;
and `choices` is now deduplicated by value, because `CategoricalDistribution` maps `1`, `1.0` and
`True` to one internal index, so a trial trained on `True` is recorded as `1` and is not
reproducible. The previous type-keyed dedup had the consumer's requirement backwards.

**Input tolerance.** `extract_json_object` now retries once on the first-`{`-to-last-`}` slice,
keeping the original error if that fails too. A single sentence of preamble previously aborted the
whole run on a node with no retry wrapper (`code_critic` targets `coder`, not the specialists). This
is a deliberate divergence from the stricter sibling nodes, logged in `context/decisions.md`.

**Test integrity.** Adversarial mutated the production `_MODEL_FAMILIES` table — gutting it to one
family, deleting three, typo'ing every alias, swapping word-boundary for substring matching — and
the suite stayed green every time, because the tests parametrized over a hand-copied table. The copy
is deleted; tests now import the real table and parametrize over its actual aliases, plus assert the
four-family set and that substring-only matches (`LGBMClassifier`) are rejected. The phase-5 smoke
test now asserts the design file actually landed with the injected `specialist`/`cv_strategy_ref`,
not merely that the subgraph ran.

**Hygiene.** `strip_outer_fence` is private (`_strip_outer_fence`, exercised through
`extract_json_object`); `_validate_bound`/`_validate_step` merged into one `_validate_numeric(...,
positive=...)`; `FEATURE_SPEC_FALLBACK_PATTERN` carries a sync-with-`config/agents/
feature_engineer.yaml` note. `docs/pipeline.md`'s Phase-5 section gained the same
pre-PR-#25 caveat `docs/agents.md` already carried.

Three items were deliberately **not** implemented and logged as OPEN discoveries instead: hoisting
the now-seventh JSON-extraction copy into `src/nodes/llm/base.py`; adding a per-specialist component
to `output_file_pattern` (a design decision across four unstarted tasks); and banning
`validation_fraction`/`early_stopping`/`n_iter_no_change`/`eval_set` — legitimate practice whose
prohibition is a modeling decision, so `FORBIDDEN_CV_KEYS`'s docstring is now honest that it is a
tripwire rather than a proof, with the escape hatch flagged for T-029/T-031.

Final: 234 unit tests across the two new test modules (205 + 29), 785 passing suite-wide,
coverage 96.7%.

## Re-review round (2026-08-12)

Security CLEAN (all 5 findings verified closed); adversarial WARNING, no blockers, 5 new findings.
Four shared one pattern: the previous round closed each hole exactly at its published reproduction
and left the same defect reachable one field over or one code path earlier. All six are closed
completely rather than at the reproduction.

1. **`_read_solution_plan` still aborted the run.** The previous round hardened `read_fold_summary`
   but not the reader called one line later in the same `_build_messages`; a truncated
   `solution_plan.json` or a moved workspace still killed the run through it. Both readers — and
   `resolve_feature_spec_ref` — now share one module-level
   `DEGRADE_ERRORS = (OSError, ValueError, RecursionError)` tuple, so consistency is a property of
   the module rather than of whoever last edited a `try` block. The sibling node modules share the
   same under-catching bug and are logged as an OPEN discovery for the `base.py` hoist, not fixed
   here.
2. **`RecursionError` escaped `read_fold_summary`.** It is a `RuntimeError`, so neither `OSError`
   nor `ValueError` caught a ~993-level nested payload. Added to the tuple; the `json.dumps` on the
   way out is now inside the guard too, since it recurses exactly as the parse does. Non-`str`
   state values are handled by an `isinstance` guard rather than by weakening the docstring.
3. **Huge ints still reached disk via `choices`/`fixed_params`.** The ±2**53 limit had gone into
   `_validate_numeric` (bounds) only, so `2**53 + 1` was written and read back as `2**53`, and
   `10**400` read back as `null` — falsifying `_is_json_scalar`'s own "survives a round trip
   unchanged" docstring. The limit now lives in `_is_json_scalar`, covering both call sites, with
   boundary tests that `±2**53` exactly is still accepted.
4. **`step > high - low` falsely rejected valid designs.** `0.3 - 0.1` is `0.19999999999999998`, so
   the exact comparison rejected `low=0.1/high=0.3/step=0.2` while `0.1/0.7/0.6` passed — Optuna
   accepts both. An input-dependent false rejection on a node with no retry wrapper is worse than
   the bug the check was added for, so the comparison now carries a relative tolerance
   (`1 + 1e-9`), far too small to readmit a step several times its range. Four float grids added as
   regression guards; the genuine over-range rejections still fail.
5. **The salvage never fired when a fence was present.** `_strip_outer_fence` raises before the
   salvage was reachable, so the two most common postamble shapes — a sentence after a closed
   fence, and a fence the model never closed — still aborted the run, i.e. the previous round's
   tolerance fix did not cover the case it was written for. Both steps are now inside the same
   `try`, and the salvage slices the raw response. It stays fail-closed: `json.loads` only ever
   sees one contiguous substring.
6. **Doc/comment accuracy.** `_PREPROCESSING_STEP_RE`'s comment claimed it stops a CV redefinition;
   it stops the *call-shaped* form only (`["stratified_kfold"]` still passes), so it now carries
   the same tripwire framing as `FORBIDDEN_CV_KEYS`. Stale references to `strip_outer_fence` and
   `_validate_step` corrected. The smoke test's `assert design["search_space"]` could not fail
   independently (the validator already forbids an empty one) and now asserts the mock's
   `n_estimators` actually round-tripped.

Final: 254 unit tests across the two new modules, 855 passing suite-wide, coverage 96.7%.
