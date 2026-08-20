---
id: T-047
phase: 2
agent: pipeline-agent
depends_on: [T-022]
status: done
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [feature_spec.json v2 schema, _validate_features, fit-scope family guards, feature_engineer prompt v2]
size: M
branch: ~
pr: https://github.com/MarianodelRio/data-science-lab/pull/36
---

## feature_spec.json v2 — single transformation primitive + `fit_scope`

**Scope:** `src/nodes/llm/feature_engineer.py` (landed in T-022),
`config/prompts/feature_engineer/`, `config/agents/feature_engineer.yaml`, the shared
scalar-validation helper in `src/nodes/llm/_experiment_design.py`, and the stale
feature-spec sentence in `config/prompts/classical_ml_specialist/v1.md`.

The current schema hard-codes three transformation categories
(`encodings`/`null_handling`/`interactions`), which cannot express the transformations a
real competition needs — cyclical encoding, log/power transforms, datetime part
extraction, aggregations, text length features, outlier clipping, and anything else the
problem calls for. Replace the three fixed lists with **one open-vocabulary primitive**,
and make train-fit scope an explicit, validated property of every entry rather than a
target-encoding special case.

`LabState` is **not** touched: `feature_spec_path` stays a `str`, and
`analysis_critic._detect_phase_stem` keeps using it as a boolean phase discriminator.

### The v2 schema

```json
{
  "features": [
    {
      "columns": ["hour"],
      "operation": "cyclical_sin_cos",
      "params": {"period": 24},
      "fit_scope": "global",
      "rationale": "hour is cyclical; raw integer encoding implies a false discontinuity 23→0"
    },
    {
      "columns": ["user_id"],
      "operation": "target_encoding",
      "params": {},
      "fit_scope": "per_fold",
      "rationale": "high-cardinality categorical, strongly predictive per EDA"
    }
  ]
}
```

- `columns` — non-empty list of non-empty strings. One column (simple transform) or N
  (interaction/combination); the same shape serves both, so the old `interactions`
  "at least 2 columns" rule disappears.
- `operation` — free string, **open vocabulary**. `feature_engineer` names whatever the
  problem needs; there is no closed catalogue.
- `params` — dict of JSON scalars (empty dict when there are none).
- `fit_scope` — `"per_fold"` | `"global"`, **required on every entry, no default**.
- `rationale` — non-empty string.

### Fit-scope safety net (generalizes the T-022 target-encoding guard)

`_validate_features` replaces `_validate_encodings`/`_validate_null_handling`/
`_validate_interactions`. Beyond the structural checks, it matches `operation` against
several keyword families; any match **requires** `fit_scope: "per_fold"` and rejects
`"global"` or a missing value:

| Family | Examples |
|---|---|
| target encoding (existing `_TARGET_ENCODING_KEYWORDS`) | `target_encoding`, `mean_encoding`, `leave_one_out`, `WOE`, `catboost`, `james_stein`, `m_estimate`, `impact_encoding` |
| statistical imputation | `median_impute`, `mean_impute`, `mode_impute`, `knn_impute`, `iterative_impute` |
| scalers / normalizers | `standard_scale`, `min_max_scale`, `robust_scale`, `z_score`, `quantile_transform`, `power_transform` |
| binning | `quantile_bin`, `kbins`, `equal_frequency_binning`, `equal_width_binning` |
| dimensionality reduction | `pca`, `svd`, `umap`, `tsne`, `nmf` |
| frequency / count encoding | `frequency_encoding`, `count_encoding` |

Matching keeps the exact mechanism `_is_target_encoding_method` already uses: whole
phrases, word boundaries (`\b…\b`), against a separator-normalized (`-`/`_` → space)
lowercase copy — so `standard-scale`, `standard_scale` and `Standard Scale` all match, and
an unrelated operation that merely mentions a family word is not flagged.

**Honest scope** (same framing as `FORBIDDEN_CV_KEYS` in `_experiment_design.py`): an
operation matching no known family gets no fit-scope check. A genuinely novel technique the
LLM invents can therefore declare `fit_scope: "global"` when it should not. That residual
risk is accepted and covered downstream by `code_critic`, exactly as the rest of the
pipeline already accepts a bounded-retry ceiling.

Note the two families are not equally severe and the code comments should say so: target
encoding is **target** leakage and breaks CV outright; scalers/binning/PCA/imputation leak
only *feature* statistics from the held-out fold, a milder optimism. Requiring `per_fold`
for both is a deliberate conservative stance, not a claim that they are the same bug.

### Out of scope (verified, no change needed)

`classical_ml_specialist.py` and the sibling specialists (T-025–T-028) consume
`feature_spec_path` as a **path only** (`resolve_feature_spec_ref` →
`design.json.feature_spec_ref`); they never read the file's contents, so the shape change
does not reach them. Only the stale prose in their prompt is touched — see below.

**Delivers:**
- `_validate_features` in `src/nodes/llm/feature_engineer.py`, replacing the three
  category validators; whitelist rebuild (`columns`/`operation`/`params`/`fit_scope`/
  `rationale`), the LLM's own object never written through
- Per-family keyword tuples + a generalized family matcher, superseding
  `_is_target_encoding_method`
- `params` validated with the JSON-scalar helper promoted to a public name in
  `src/nodes/llm/_experiment_design.py` (its module docstring updated: it is no longer
  Phase-5-only), rather than a fourth copy of the same check
- `config/prompts/feature_engineer/v2.md` explaining the single primitive, the open
  vocabulary and `fit_scope`; `prompt_version: v2` in `config/agents/feature_engineer.yaml`
- `config/prompts/classical_ml_specialist/v1.md`: the sentence naming "column encodings,
  null-imputation strategies, and feature interactions" refreshed to the v2 vocabulary
  (in-place edit, no `prompt_version` bump — wording only, no semantic change)

**Done when:**
- [ ] a valid spec with both a 1-column and an N-column entry is written unchanged, rebuilt from the whitelist
- [ ] a missing `features` key, a non-list `features`, or a non-object entry raises `ValueError` naming `feature_engineer`
- [ ] `columns` empty, non-list, or containing a blank/non-string raises `ValueError`
- [ ] `operation` or `rationale` missing/blank/non-string raises `ValueError`
- [ ] `fit_scope` absent, or any value other than `"per_fold"`/`"global"`, raises `ValueError`
- [ ] for **each** family, an operation matching it with `fit_scope: "global"` raises `ValueError`, and with `fit_scope: "per_fold"` is accepted (parametrized over representative names + separator variants)
- [ ] an operation merely mentioning a family word without matching a whole phrase is not flagged
- [ ] an unrecognized operation with `fit_scope: "global"` is **accepted** (asserts the documented residual risk)
- [ ] `params` containing a nested object, `NaN`/`Infinity`, or an int beyond ±2**53 raises `ValueError`; an empty `params` is accepted
- [ ] `features: []` is accepted
- [ ] `state["feature_spec_path"]` is still set (load-bearing for `analysis_critic._detect_phase_stem`)
- [ ] `_MOCK_FEATURE_SPEC` in `tests/integration/phases/test_phase_subgraphs_smoke.py` migrated to v2 and the smoke passes
- [ ] `config/agents/feature_engineer.yaml` resolves `prompt_version: v2` and `v2.md` loads
- [ ] unit tests with mocked LLM, no network; `src/nodes/llm/feature_engineer.py` ≥85% coverage
- [ ] `ruff check . && ruff format --check .` and `mypy src/` pass
- [ ] `docs/pipeline.md` documents the v2 `feature_spec.json` schema — it is the contract `coder` (T-029) generates against
- [ ] `context/decisions/T-047.md` entry recording the v2 primitive and the family-based fit-scope guard, explicitly superseding the `fold_aware` decisions now in `context/decisions/T-022.md` (the two `2026-08-11 — T-022 [pipeline-agent]` entries; they were at `context/decisions.md:749-780` before the dev-team v1.4 migration split that file per task)

## Completed

**What was implemented**

- **`src/nodes/llm/feature_engineer.py` — schema v2.** `_validate_encodings`/`_validate_null_handling`/`_validate_interactions`/`_is_target_encoding_method` deleted. In their place: `_validate_features` over one open-vocabulary `features` list, with `_validate_feature_entry` performing a whitelist rebuild into exactly `columns`/`operation`/`params`/`fit_scope`/`rationale`, in that order — the LLM's own object is never written through, so v1's `fold_aware`/`column`/`method`/`strategy`/`type` are dropped. Field order is load-bearing: `operation` is validated before `fit_scope`, because the family guard needs a validated string.
- **Six per-family keyword tuples + `_matched_fit_scope_family`**, superseding the single-family `_is_target_encoding_method`. T-022's curated target-encoding tuple is carried over byte-for-byte, and so is the matching mechanism (whole-phrase `\b…\b` against a separator-normalized lowercase copy); only the scope generalized. The matcher returns the family *label* rather than a bool, so the error message names the family for critic-retry feedback. Tuples shipped exactly as ruled (Q2): `mice` in, bare `lda` out, no bare stems.
- **`params` via the promoted shared helper.** `_experiment_design._is_json_scalar` → public `is_json_scalar` (three call sites renamed, no behavior change) and imported here, accepting a scalar **or a flat list of scalars** — the `_validate_fixed_params` predicate verbatim. `params` keys are also validated as non-empty strings. Module docstring of `_experiment_design.py` updated: it now declares `DEGRADE_ERRORS` and `is_json_scalar` as Phase-4 exports while the `design.json` contract itself stays Phase-5-only.
- **Degrade-safe readers (Adjustment 7).** Both `feature_engineer` readers adopt `DEGRADE_ERRORS` plus an `isinstance(path, str)` guard, with `json.dumps` moved inside the `try` — the shape `_experiment_design.read_solution_plan` already had. They stay private per-module copies; only the caught set changed.
- **`config/prompts/feature_engineer/v2.md`** (new; `v1.md` kept on disk) and `prompt_version: v2` in the yaml. The prompt leads with Adjustment 6's general rule — *anything fitted must declare `per_fold`; only stateless row-wise transforms may declare `global`* — stated before the family list, with `groupby_user_mean_amount` as the worked example of a fitted operation that matches no family. The family section then names the recognized terms per family and closes with "this list is a floor, not the boundary".
- **Stale sibling sentences** refreshed in place, no version bump: `experiment_designer/v1.md` and `classical_ml_specialist/v1.md` (the latter mirroring the already-correct `nlp_specialist/v1.md`).
- **`docs/pipeline.md`** — authored a new `### Design (Phase 4)` section between Baseline and Implementation, covering `solution_architect` at outline depth, `feature_engineer`, `analysis_critic`'s schema-neutrality, and a `#### The feature_spec.json contract (v2)` subsection modeled on the existing `design.json` one (key table, the six families, the two-tier severity paragraph, the honest-scope caveat, the `preprocessing` tie-break, and a v1 → v2 note).
- **`docs/agents.md`** — added the four-step `## Changing a prompt` subsection (Orchestrator ruling Q1, option b).
- **Tests** — `tests/unit/nodes/llm/test_feature_engineer.py` rewritten around `_entry`/`_entry_with`/`_respond` helpers; 199 tests. Every done-when line has a named test, both family directions parametrize over one shared `_FAMILY_OPERATIONS` list, and the degrade-safe readers get their own section against a **real** `WorkspaceManager` on `tmp_path` (a mocked workspace cannot raise the real `ValueError` from `_resolve`). `_MOCK_FEATURE_SPEC` migrated to v2 in `tests/fixtures/graph_mocks.py` with one `global` and one family-matching entry, so both branches of the guard run in the graph-driven tests.

**Coverage:** `src/nodes/llm/feature_engineer.py` — **99%** (121 statements, 1 missed), well above the task's 85% bar, which the global `--cov-fail-under=70` gate does not enforce. The single uncovered line is the pre-existing, documented-unreachable `"fence has no closing delimiter"` branch in `_strip_outer_fence` (`text.endswith("```")` is checked first, so `rfind` cannot return `-1` once a newline exists). Full suite: 1994 passed, total coverage 97.60%. `ruff check`/`ruff format --check`/`mypy src/` all clean. The two graph-driven consumers (`test_phase_subgraphs_smoke.py`, `test_checkpointer.py`) verified green separately.

**Deviations from plan**

- **Both context files written.** The `coder-complete` steering says not to write `context/decisions/T-XXX.md`, but this task's done-when requires it and the plan dictated its four entries verbatim. The Orchestrator ruled: write both. The steering rule exists to stop coders freelancing decision records, which is not the case when the content is specified.
- **`_validate_operation` and `_validate_rationale` merged** into one `_validate_text_field(index, field, value)`. The plan showed them as two functions of identical shape; a single parameterized helper is the same behavior and the same error wording without the copy.
- **Two extra stale comments in `_experiment_design.py` refreshed.** The `DEGRADE_ERRORS` comment listed `feature_engineer` among the modules "still catching `OSError` alone", and `read_solution_plan`'s docstring called its copy "divergent, `OSError`-only". Both became false in this PR, so both were corrected — wording only, no behavior.
- **The new `docs/pipeline.md` section is ~122 lines**, above the plan's stated "60-80" but in line with the proportion the plan actually asked for (`### Baseline (Phase 3)` is 52 lines and `#### The design.json contract` is 61 — 113 together). Nothing was rewritten; it is all new content.
- **The two fence tests from plan §4.4 were added unconditionally** rather than only if coverage fell short. They are two lines each and cover two of the three fence branches; the third is the unreachable one the plan says to leave alone.
- **Task file moved `available/` → `in-progress/`** to match `origin/main`, which `dt-claim` had already renamed. Without this the branch would have modified a path main deleted, producing a conflicting PR.

**Key decisions**

- **Family tuples and the matcher stay private to `feature_engineer.py`.** T-020's hoisting rule triggers at the third copy and there is exactly one; T-032's "shared module only when all consumers land together" does not apply with a single consumer (`coder` consumes the validated artifact, not the validator). Putting feature-spec vocabulary into `_experiment_design.py` would blur its declared purpose, whereas importing two general-purpose utilities *from* it does not — the dependency runs one way only.
- **`fit_scope` matching is exact-token**, deliberately not case-folded or separator-normalized, unlike *operation* matching. `coder` branches on this value, so only `per_fold`/`global` may reach the artifact. `value not in _FIT_SCOPES` also rejects `True`/`False`/`None`/missing in one branch, so no `isinstance(bool)` special case is needed here.
- **`frequency_encoding_excluding_target_leak` is now flagged** — T-022's canonical false positive. This is deliberate, not a regression: it is still not matched as *target* encoding (T-022's actual property), it simply belongs to the frequency/count family v2 added, and frequency encoding does learn global category counts. The old false-positive test was converted into a positive-flag test and the false-positive done-when is served by seven different examples.
- **`tests/fixtures/graph_mocks.py` touched only for `_MOCK_FEATURE_SPEC` and its comment block**, per Adjustment 4. Its `_make_llm_side_effect` docstring still references `config/prompts/{name}/v1.md` generically, which is now imprecise for `feature_engineer` — left deliberately, so a reviewer does not read it as an oversight.
- **Scope held.** `LabState`, `spec.md`, `design.json`/`preprocessing`/`_PREPROCESSING_STEP_RE`/`DESIGN_KEYS`, the `_strip_outer_fence`/`_extract_json` duplication (8 fence copies + 8 extractors across 8 files — unchanged by this PR; the count is corrected in the discovery, see the review fix round below) and the T-032 Phase-4-reads-Phase-6 discovery were all left untouched. `spec.md:502-512` is knowingly stale — the Orchestrator runs `/refine` post-merge.
- **Discoveries:** the B-001 → T-047 entry in `context/discoveries/legacy.md` is marked `resolved in T-047`. `context/discoveries/T-047.md` carries three open entries — the v2 contract and the `fit_scope`-beats-`preprocessing` tie-break for T-029; the T-024 reader discovery closed for this module only (still open for `baseline_designer`/`solution_architect`/`_research_common`); and the accepted residual risk that an unmatched operation may declare `global`, with `code_critic`'s rubric named as the only downstream net.

**Dependencies added:** None.

### Review fix round

Review returned **WARNINGS, no blockers**. All findings addressed; the Orchestrator revoked ruling
Q2 ("ship the tuples exactly as written") on the strength of the review's executed evidence, so the
tuples themselves changed — in these ways and no others.

**Keyword-guard corrections (the four WARNINGs).** Every claim was verified by running
`_matched_fit_scope_family` against the real code, before and after.

- [CQ-f4dda81e] `_SCALER_KEYWORDS` **lost** `"unit norm"` and `"l2 normalize"`. sklearn's
  `Normalizer` scales each sample by its own norm and learns nothing from any other row: it is
  stateless, so `global` is the declaration the v2 prompt asks for. Flagging it made a *correct*
  response raise, and `LLMNode.__call__` (`src/nodes/llm/base.py`) has no `try/except`, so that
  terminated the Phase 4 run. A comment in their place records the exclusion as deliberate.
- [CQ-b2a0b5bc] `_SCALER_KEYWORDS` **gained** `maxabs`, `max abs scaling`, `zscore`, `standardize`,
  `standardization` (`max abs scale` was already there). `standardize`, `maxabs_scale` and
  `zscore_normalization` all returned `None` and were written `global`. This also closes the
  tuple's own asymmetry — it carried the concatenated `minmax` but not `maxabs`, `z score` but not
  `zscore`.
- [CQ-67bf82eb] `_STATISTICAL_IMPUTATION_KEYWORDS` **gained** the verb-first and pandas phrasings:
  `impute median/mean/mode`, `fillna median/mean/mode`, `median/mean/mode fill`, `simple impute`.
  The tuple tripled every `median impute/imputation/imputer` variant while missing `fillna_median`,
  the single most probable name an LLM emits for this transformation.
- [CQ-6e404ca1] `_TARGET_ENCODING_KEYWORDS` **gained** `target encode` and `target encoder`. This
  tuple is therefore **no longer carried byte-for-byte from T-022** (the bullet above, written for
  the pre-review commits, is superseded on that point). Highest severity of the six — actual target
  leakage — and the gap exists on `main` too; fixed here because this PR promotes the tuple to the
  sole floor for that family, and because this PR's own `_FREQUENCY_ENCODING_KEYWORDS` already
  lists the noun *and* verb form.
- **camelCase normalization**, required to actually close the one above. `_normalize_operation` now
  inserts a space at a lowercase/digit→uppercase boundary and after an uppercase run followed by a
  capitalized word, before lowercasing: `TargetEncoder` → `target encoder`, `MinMaxScaler` →
  `min max scaler`, `KNNImputer` → `knn imputer` (not `k n n imputer`), `TruncatedSVD` →
  `truncated svd`, while `PCA` → `pca` and `WOE` → `woe` are untouched. These are sklearn's real
  class names and were reachable by **no** keyword in any tuple. `_matched_fit_scope_family` now
  matches against **both** the split and the unsplit normalization: splitting alone would have
  broken a pre-existing match, since `CatBoost encoding` splits to `cat boost encoding` and no
  longer hits the concatenated `catboost` keyword. Matching both adds the class-name shapes without
  trading anything away, and cannot introduce a false positive the unsplit form did not already
  have. Documented non-coverage, stated rather than assumed: an unseparated all-lowercase run
  (`targetencoder`) and a digit-run boundary (`Top10Encoder` → `top10 encoder`).
- **Regression check, explicitly:** the "merely mentions a family word" suite (`log_transform`,
  `standard_deviation_ratio`, `mean_of_last_3_orders`, `count_distinct_categories`, `target_lag_1`,
  `datetime_part_extraction`, `text_length`) and the "unrecognized operation accepted with global"
  suite (`groupby_user_mean_amount`, `winsorize_clip_outliers`, `quantile_clip_outliers`,
  `rolling_ratio_v3`) both stay green. Those two done-whens are in direct tension with every
  keyword added, and neither moved.

**Two new validation rules** (human-approved from the adversarial findings).

- [ADV-ed8fe4df] duplicate column names within one entry are rejected. `["amount","amount"]` with
  `operation: "ratio"` validated before and produced a constant-1 feature at T-029 that would read
  as a modeling problem. `solution_architect` sets the precedent with normalized-duplicate
  `model_families`.
- [ADV-0497d105] two entries sharing a `(tuple(columns), operation)` pair are rejected, naming both
  indices. The key is the pair, **not** full-entry equality: differing `params` change the values
  but not the derived column's name, so that is a collision too. `columns` stays ordered, so
  `["a","b"]` and `["b","a"]` remain two legal entries.

**Nitpicks.**

- [CQ-157f757d] `_validate_text_field` now returns `value.strip()`. `operation: "  target_encoding  "`
  passed the truthiness check and reached the artifact padded, and `coder` string-matches
  `operation`. Stripping happens before the family guard sees it, so padding cannot slip a fitted
  operation past the check either — there is a test for that.
- [CQ-74912f39] the family parametrization now carries `(operation, expected_family)` and both
  directions assert the family **label**, not just `match="per_fold"` (every family's message
  contains that string, so a keyword relocated into the wrong tuple would have passed all 56
  cases). Still one shared `_FAMILY_OPERATIONS` list driving accept and reject together.
- [CQ-2ee8c605] `context/discoveries/T-047.md`'s "unchanged at 9" corrected. The *unchanged* part
  was right; the number was copied forward from the T-024 discovery text. Measured on this branch:
  **8 `_strip_outer_fence` definitions and 8 JSON extractors across 8 files** — and the extractors
  are not one function eight times (five `_extract_json`, two `extract_json_object`, one
  `extract_json_array`, with the shared ones taking a `node_name`/`specialist` parameter the
  node-private ones hard-code), which the hoist will have to reconcile.

**Records and docs.** `context/discoveries/T-047.md`'s T-029 entry gained three items the review
surfaced: `schema_version` was considered and deliberately **not** added (human ruling — it would
add a key to the just-pinned contract; no live path breaks, since `analysis_critic` reads the file
as raw text and `resolve_feature_spec_ref` always points at the current iteration, but several
`design/iteration_{N}/` generations coexist in a workspace); [SEC-4114715b] `columns` entries are
unconstrained free strings originating in the user's Kaggle dataset and must be **escaped** in
`coder`'s codegen; [SEC-cb459b3c] no size or cardinality bound exists, low-risk because
`max_tokens: 4096` bounds it in practice, but noted against `_delivery_common.MAX_INJECTED_CHARS`'s
convention since the artifact is re-injected verbatim into `analysis_critic`'s prompt.
`context/decisions/T-047.md` gained one entry recording the tuple change, the camelCase extension
and both deferrals. `config/prompts/feature_engineer/v2.md` and `docs/pipeline.md` were updated to
match the tuples exactly — the added terms, the removed row-wise normalizers (with a short note
telling the LLM that `l2_normalize` is correctly `global`), the camelCase note, and the two new
structural rules. Prompt and validator vocabulary must not drift; that is what T-022's decision
record exists for.

**Not fixed, deliberately:** the `schema_version` key and the size/cardinality bounds — both ruled
out by the human and recorded as discoveries for T-029 instead. `spec.md:502-512` stays knowingly
stale (`/refine` runs post-merge).

**Verification after the fix round:** full suite **2080 passed** (285 in
`test_feature_engineer.py`), total coverage 97.61%; `src/nodes/llm/feature_engineer.py` at **99%**
(140 statements, 1 missed — the same pre-existing unreachable `"fence has no closing delimiter"`
branch), against the task's 85% bar. `ruff check .`, `ruff format --check .` and `mypy src/` clean.
`tests/integration/phases/test_phase_subgraphs_smoke.py` + `tests/unit/graph/test_checkpointer.py`
green (12 passed).

#### Round 2 — five precise fixes, one of them reversing round 1

A second review pass, again all findings verified by executing `_matched_fit_scope_family`.

- [CQ-4033aa95] `_SCALER_KEYWORDS` **loses** `standardize` and `standardization` — the two terms
  round 1 added above. They are bare stems in exactly the sense the module comment already
  excludes `normalize`/`normalization` for: "standardize" means "make uniform" at least as often as
  it means z-scoring. `standardize_address_format`, `standardize_text_case`,
  `standardize_country_codes`, `StandardizePhoneNumber` and `standardize_units_to_metric` all
  matched and therefore **aborted the run**. Both terms are now named in the excluded-bare-stem
  comment so nobody re-adds them; z-scoring stays covered by `standard scale(r)`, `zscore`,
  `z score`, `minmax`, `maxabs`. The severity comment was rewritten with the reason this matters:
  the guard's conservatism is **not symmetric**. It raises `ValueError` rather than coercing
  `fit_scope`, and nothing catches it — so over-matching is a dead run with nothing behind it,
  while under-matching is a silent leak with three layers behind it (the prompt's general rule,
  `code_critic`'s rubric, the family's other keywords). Round 1's "over-matching is cheap" premise
  was simply false for this guard.
- [ADV-4acdc3c6] `_TARGET_ENCODING_KEYWORDS` **gains** `cat boost`. The tuple carries `catboost`
  concatenated, so the camel split turned `category_encoders`' real class name `CatBoostEncoder`
  into `cat boost encoder`, which matched nothing — in the target-leakage family, the most severe
  of the six. `catboostencoder` (unseparated lowercase run) remains the documented non-coverage.
- [CQ-2bc3fb49] `_STATISTICAL_IMPUTATION_KEYWORDS` **gains** `most frequent imputer`. The prompt
  promised "the same three forms for `mean_`, `mode_` and `most_frequent_`" but the tuple had only
  two for `most_frequent_`. Fixed on the tuple side, not by weakening the prompt —
  `SimpleImputer(strategy="most_frequent")` is real sklearn. All four stems now carry all three
  forms, re-verified by execution, so the prompt's claim is true; no other combination over-claims.
- [ADV-7a7ea912] `_normalized_operation_variants`' docstring no longer claims the dual-variant
  match "cannot introduce a false positive the unsplit form did not already have". It keeps the
  half that is provable — the tuple always contains the plain separator-collapsed form, so the
  match set is a strict **superset** and nothing is lost — and states plainly that it matches
  strictly more strings and can therefore surface a new false positive (`StandardizeTextCase`,
  `ModeFillColorFlag`, `ImputeMeanFlagOnly`, `FillnaMeanIndicator`, `TargetEncoderFreeBaseline` all
  match split, none unsplit). Every such case has a snake_case twin that false-positives under both
  forms, so the mitigation is keyword discipline, not a property of the splitting.
- [CQ-819a2a50] `context/discoveries/T-047.md`'s arithmetic corrected a second time: "four
  `_common`/shared" + "five node-private" summed to 9 against a stated total of 8. Measured from
  the `grep` line: **three** parameterized extractors + five node-private = 8, and the eight
  `_strip_outer_fence` copies split the same way. All three parameterized ones are now named
  inline.

`config/prompts/feature_engineer/v2.md` and `docs/pipeline.md`'s family table track all three tuple
changes; the prompt additionally tells the LLM to name z-scoring `standard_scale`/`z_score` rather
than a bare `standardize`, and the docs' severity paragraph carries the asymmetry argument.
`context/decisions/T-047.md` gained a round-2 entry whose substance is that asymmetry; under
Discarded it records that keeping `standardize` behind a qualifier (`standardize numeric`) was
rejected as relying on phrasing the LLM has no reason to produce, and that the round-1 entry's
"discarded: adding `cat boost`" is reversed — matching both variants and carrying `cat boost` are
complementary, not alternatives.

**Tests.** `_FAMILY_OPERATIONS` drops the two `standardize` rows and gains `CatBoostEncoder`,
`cat_boost_encoder`, `most_frequent_imputer`, `MostFrequentImputer` — still one shared
`(operation, expected_family)` list driving both directions. A new
`test_standardize_prefixed_stateless_operation_with_global_is_accepted` pins the six removed false
positives as **accepted** with `global`. The two tension suites gained camelCase twins of every
case (`LogTransform`, `MeanOfLast3Orders`, `GroupbyUserMeanAmount`, `RollingRatioV3`, …), since the
split normalization is the mechanism that could break them.

**Verification after round 2:** full suite **2104 passed** (+24 cases), total coverage 97.61%;
`src/nodes/llm/feature_engineer.py` at **99%** (140 statements, 1 missed — the same unreachable
fence branch), `test_feature_engineer.py` **309 passed**. `ruff check .`, `ruff format --check .`
and `mypy src/` clean. `tests/integration/phases/test_phase_subgraphs_smoke.py` +
`tests/unit/graph/test_checkpointer.py` green (12 passed).
