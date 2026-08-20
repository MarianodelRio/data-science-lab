---
id: T-047
phase: 2
agent: pipeline-agent
depends_on: [T-022]
status: in-progress
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [feature_spec.json v2 schema, _validate_features, fit-scope family guards, feature_engineer prompt v2]
size: M
branch: feature/T-047-feature-spec-v2-fit-scope
pr: ~
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
