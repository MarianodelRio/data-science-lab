---
id: T-031
phase: 2
agent: pipeline-agent
depends_on: [T-011]
status: done
folders: ["src/nodes/compute/", "config/phases/"]
outputs: [score_evaluator node, feature_importance_extractor node, feature_importance_N.json]
size: M
branch: feature/T-031-node-evaluation-compute
pr: "https://github.com/MarianodelRio/data-science-lab/pull/33"
---

## Nodes: score_evaluator + feature_importance_extractor (Pipeline Phase 6, compute)

**Scope:** two `ComputeNode` subclasses. Pure Python, no LLM.

**Delivers:**
- `score_evaluator`: reads latest experiment `results.json`; compares vs `baseline_score` and previous iterations; sets `state["last_score"]`, `state["score_delta"]`, updates `state["best_score"]`/`best_experiment_path` **only if improved**, and increments `iterations_without_improvement` when not improved
- `feature_importance_extractor`: computes SHAP for tree models, writes `reports/feature_importance_{iteration}.json`; **skips silently for neural models** (per design)

**Done when:**
- [ ] score_evaluator sets `score_delta = last_score - best_score_before` correctly (unit test with fixtures)
- [ ] `best_experiment_path` updates only when the new score is better; stays put otherwise
- [ ] `iterations_without_improvement` increments on a non-improving score and resets on improvement
- [ ] feature_importance_extractor writes a JSON for a tree model and returns early (no file) for a neural model
- [ ] no LLM import in either module
- [ ] unit tests cover improve / no-improve / neural-skip
- [ ] `docs/pipeline.md` invariant (best only-improves) noted

## Completed

Implemented per the human-approved plan (`T-031-plan.md`), which supersedes two bullets above: (1)
`score_delta` is `0.0`, not `+inf`, on the first evaluation (the `-inf` best_score sentinel would
otherwise make the delta non-finite); (2) `feature_importance_extractor` **extracts** a pre-computed
`{feature: value}` payload from `results.json` — it never computes SHAP itself, so "no file for a
neural model" generalizes to "no ranked-features artifact for any model family outside a curated
tree-ensemble allow-list" (an allow-list, not a neural-specific deny-list).

**Files added:**
- `src/nodes/compute/_evaluation_common.py` — private shared module (no class named `_evaluation_common`,
  so `resolve_node` never mistakes it for a node). Experiment-directory resolution
  (`experiment_dir_from_state`/`candidate_experiment_dirs`/`read_experiment_results`) is a verbatim
  port of `code_critic`'s own private helpers (`code_critic.py:99-158`) — ported rather than imported
  because that module lives in `src/nodes/llm/`, which pulls in `langchain_core`. Also holds
  `relative_to_workspace`, `read_json_dict` (degrade-to-`{}`), and `resolve_iteration`.
- `src/nodes/compute/score_evaluator.py` — `ScoreEvaluatorNode`. Sole writer anywhere in `src/` of
  `last_score`, `score_delta`, `best_score`, `best_experiment_path`, `iterations_without_improvement`.
  Normalizes minimize-oriented metrics (curated set, separator-normalized name matching) to
  higher-is-better before any comparison; strict `>` for improvement (a tie never updates the best);
  `iterations_without_improvement` increments even when no valid score is obtainable (see the liveness
  decision below); writes `reports/score_evaluation_{iteration}.json` unconditionally, never emitting
  `inf`/`nan` (downgraded to JSON `null`). `delta_vs_baseline` is informational only, gated on
  `results.json`'s optional `metric` field normalizing into `{accuracy, r2, rsquared, score}`.
- `src/nodes/compute/feature_importance_extractor.py` — `FeatureImportanceExtractorNode`. Always
  returns `{}` (no `LabState` field exists for this artifact). Gated by an explicit tree-ensemble
  `model_family` allow-list read from `design.json`; extracts and ranks `results.json`'s
  `feature_importance`/`feature_names` payload by absolute magnitude into
  `reports/feature_importance_{iteration}.json`. **No `shap` import anywhere** in the module.

**Tests added** (88 new unit tests + 1 integration block, all passing):
- `tests/unit/nodes/compute/test_evaluation_common.py` (25 tests)
- `tests/unit/nodes/compute/test_score_evaluator.py` (37 tests, including the mutation-killer set for
  tie handling, the finite-vs-`-inf` `score_delta` branch, the minimize/maximize sign flip, the
  `iterations_without_improvement` `+=`/reset/liveness paths, and the never-write-`inf`/`nan` guard)
- `tests/unit/nodes/compute/test_feature_importance_extractor.py` (26 tests)
- `tests/integration/phases/test_phase_subgraphs_smoke.py` — added a `phase6_evaluation` assertion
  block (bare-state run: `evaluated is False`, `best_score` untouched at `-inf`,
  `iterations_without_improvement == 1`)

**Docs:** `docs/pipeline.md` — new `### Evaluation (Phase 6)` subsection, two `## Node classification`
rows, the first real `## Invariants` entry (CLAUDE.md invariant #3), and the § State note updated to
say polarity normalization is implemented rather than merely assigned.

**Decisions logged in `context/decisions.md`** (2026-08-17, T-031): the shared-module rationale, the
score-direction normalization design, the `delta_vs_baseline` comparability contract, the liveness
override (**`iterations_without_improvement` increments even when no valid score is obtainable** —
overrides what the original plan proposed, because `src/graph/supervisor.py:31-33` makes this counter
the *only* exit from the Phase 6 → Phase 4 loop and not incrementing on "nothing to evaluate" would
loop the pipeline forever on a permanently-broken `results.json`), the deliberate non-increment of
`current_iteration`, and why `shap` is never imported.

**Discoveries logged in `context/discoveries.md`** (2026-08-17): an OPEN entry to `coder` (T-029)
pinning the `results.json` contract this task assumes; a NOTE to T-032 confirming `current_iteration`
is not incremented here; a RESOLVED entry closing the 2026-08-04 `infra-agent (T-002)` polarity
discovery.

**Verification:** `pytest --cov=src --cov-fail-under=70` → 1297 passed, 13 pre-existing failures in
`tests/tools/test_rag.py` (unrelated to this task's folders, confirmed present on `main` before any
change here), global coverage 96.09%. `score_evaluator.py` alone: 100% coverage (target ≥85%).
`ruff check .` and `ruff format --check .` both clean. `mypy src/`: no issues in 68 source files.

### Review-response round (2026-08-18)

Code review results: code-quality clean, smoke-tester clean 8/8, mutation 95% (threshold 80),
security WARNING (2), adversarial WARNING (1 treated as blocker). Six findings fixed, all inside
`src/nodes/compute/`:

1. **BLOCKER — output-filename/directory-read divergence.** `resolve_iteration` (names the output
   file) and directory resolution (`candidate_experiment_dirs`, names what gets *read*) were two
   independent lookups that could disagree: an `experiments` entry with a valid `iteration` key but
   an unusable/absent `path` made the node read the well-known fallback directory while filing the
   report under the entry's claimed number — a stale read silently mislabeled as a different
   experiment, with a false-positive `is_improvement` possible. Fixed via
   `_evaluation_common.resolve_output_iteration`: the output number now comes from the *resolved*
   `experiment_dir`'s own trailing `exp_<N>`, falling back to `resolve_iteration` only when the
   directory doesn't match that shape; a new `experiment_resolution_warning` field (both nodes'
   artifacts) names the entry's claimed iteration and the fallback actually used when they diverge.
   Directory-resolution precedence itself (the verbatim `code_critic` match) is unchanged.
2. **Non-finite subtraction results.** `score_delta`/`delta_vs_baseline` are each a subtraction of two
   individually-finite operands, and the *result* was never re-checked — two large finite floats of
   opposite sign can overflow to `inf`. Both are now guarded with `math.isfinite` and degrade
   explicitly (`score_delta` → `0.0`; `delta_vs_baseline` → `None`); `baseline_comparison_made` now
   reflects whether a finite delta was actually produced, not mere eligibility.
3. **`_rank_importances`' `total = sum(...)` overflow.** Two extreme-magnitude entries are enough to
   overflow the sum, silently zeroing every `normalized_importance`. Now guarded explicitly and
   recorded as `importance_total_overflowed` in the artifact (rank order/individual magnitudes stay
   correct either way).
4. **Unbounded `feature_importance` payload.** Capped at `_MAX_RANKED_FEATURES = 3000` (largest by
   absolute magnitude); truncation is recorded in-band via `features_truncated`/
   `original_feature_count`, mirroring `code_critic._truncate`'s marker precedent.
5. **Mutation survivor: `bool` rejection in `_coerce_finite_float`.** No test fed a JSON boolean as a
   score, so removing the `isinstance(value, bool)` guard survived. Added tests for `cv_score`,
   `best_score`, and `baseline_score` as booleans (code already handled all three correctly).
6. **NIT — `candidate_experiment_dirs` now routes `current_iteration` through `_coerce_iteration`**
   instead of reading it raw, for consistency with every other iteration lookup in the module.

**Not fixed in code (by design):** the polarity-persistence gap adversarial review's repro3 found
(`best_score` is stored sign-normalized with no record of which `direction` produced it, so a
`problem_definition.json` that goes unreadable between iterations can silently flip which experiment
counts as "best"). The real fix is a polarity field on `LabState` — a protected contract requiring
human approval, out of scope here. Logged as an OPEN entry in `context/discoveries.md` (2026-08-17)
with the concrete repro; noted in `docs/pipeline.md`'s § Evaluation (Phase 6). Explicitly did not
half-mitigate by reading a previous iteration's own report cross-iteration, per review's instruction.

**Tests added:** 24 new unit tests across `test_evaluation_common.py` (11: `entry_iteration`,
`iteration_from_experiment_dir`, `resolve_output_iteration`, boolean `current_iteration`),
`test_score_evaluator.py` (8: boolean coercion x3, overflow guards x2, resolution-warning divergence
x3) and `test_feature_importance_extractor.py` (5: overflow x2, truncation cap x2, resolution
warning x1), plus one existing filename test updated to assert the corrected (bug-fixed) behavior.

**Re-verification:** `pytest --cov=src --cov-fail-under=70` → **1321 passed**, same 13 pre-existing
`tests/tools/test_rag.py` failures (unrelated, confirmed present before this task started), global
coverage **96.17%**. `score_evaluator.py` alone: **100%** coverage (45 tests, target ≥85%).
`ruff check .` and `ruff format --check .` both clean. `mypy src/`: no issues in 68 source files.
