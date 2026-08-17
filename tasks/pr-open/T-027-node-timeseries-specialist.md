---
id: T-027
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: pr-open
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [timeseries_specialist node, experiment design with Optuna search space]
size: S
branch: feature/T-027-node-timeseries-specialist
pr: "https://github.com/MarianodelRio/data-science-lab/pull/30"
---

## Node: timeseries_specialist (Pipeline Phase 5)

**Scope:** `timeseries_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs temporal experiments: lag features, rolling statistics, ARIMA/Prophet univariate baselines; with an Optuna search space
- Writes `experiments/exp_{next_id}/design.json`; activated only when temporal structure exists; must respect temporal CV (no future leakage)
- `model_role: reasoning`

**Done when:**
- [x] with a mocked LLM the node writes an experiment `design.json` containing a `search_space` and temporal features
- [x] the design references the frozen (time-aware) folds and never uses future data
- [x] agent YAML + prompt v1 exist and load
- [x] unit test with mocked LLM, no network
- [x] `docs/agents.md` row added

## Completed

**Implemented** — the four-part LLM-node pattern, nothing shared:
- `config/agents/timeseries_specialist.yaml` — `model_role: reasoning`, `prompt_version: v1`,
  `tools: []`, `output_file_pattern: experiments/exp_{iteration}/design.json`, `max_tokens: 4096`.
  `exp_{iteration}`, not the task's `exp_{next_id}`: no id allocator exists yet (nothing in `src/`
  increments `current_iteration` — that is T-031/T-032's problem), so the sibling specialists'
  pattern is followed exactly.
- `config/prompts/timeseries_specialist/v1.md` — first line pinned to
  `# System prompt — timeseries_specialist` (`tests/fixtures/graph_mocks.py` routes mocked responses
  by matching that exact header).
- `src/nodes/llm/timeseries_specialist.py` — `TimeseriesSpecialistNode(LLMNode)`, `name` as a plain
  class attribute. Overrides `_build_messages` (inject `## Solution plan` / `## Frozen CV folds` /
  `## Feature spec reference`) and `_write_output` (extract → validate → `write_json`) only. Uses the
  shared `read_solution_plan`/`read_fold_summary`/`resolve_feature_spec_ref`/
  `validate_experiment_design` helpers — no node-local reader copies. No `_build_output_state`
  override and no new `LabState` field (`coder` reads the well-known path; `src/state.py` is a
  protected contract).
- `tests/unit/nodes/llm/test_timeseries_specialist.py` — LLM and `WorkspaceManager` mocked, no
  network, no real filesystem writes.

**Also changed** (all outside the node's own four files, each for a stated reason):
- `tests/unit/nodes/compute/test_specialist_selector.py` — **removed a live-network landmine.** Two
  tests routed the *real* `resolve_node` at `timeseries_specialist` precisely because it had not
  landed; once it did, they constructed a real chat model and attempted a live API call. The NoOp
  path now runs through a sentinel `NEVER_LANDING_SPECIALIST` that no module will ever implement,
  and the selector-level test patches the module-private `_select_by_signal` so the real
  `resolve_node` still runs but can never be handed a real specialist name. Added the landed-case
  test. The re-pointing chain is now terminated and documented as such — T-028 must not re-point.
- `tests/fixtures/graph_mocks.py` — `_MOCK_TIMESERIES_DESIGN` + dispatch row (the generic fallback
  is a fenced-python narrative that fails `extract_json_object`).
- `tests/integration/phases/test_phase_subgraphs_smoke.py` — new
  `test_phase5_subgraph_routes_forecasting_plan_to_timeseries_specialist`, restoring at a better
  level the selector→`resolve_node`→real-node→file-on-disk coverage the rewritten unit test gave up.
- `src/nodes/llm/_experiment_design.py` — module docstring's landed/pending list only.
- `docs/agents.md` (row), `docs/pipeline.md` (node bullet, the now-wrong "still falls back to
  NoOpNode" line, node-classification row), `context/decisions.md`, `context/discoveries.md`.

**Decisions and why** (full text in `context/decisions.md`):
- *Five canonical model families* — `arima`, `prophet`, `exponential_smoothing`,
  `gradient_boosting_lags`, `linear_lags` (human-checkpoint decision, pinned by a test).
- *Alias table, corrected during review after an adversarial pass found a whole class of collisions
  the original 39 tests structurally could not see:*
  - **A bare `"linear"` is not aliased.** It is a *trend* word far more often than a family word
    here, and it co-occurs with every other family — "Holt's linear trend method" (the textbook
    name), "Prophet (growth=linear)" (Prophet's default), "ARIMA with linear trend", "LightGBM
    linear_tree", "gradient boosting with linear base learners". All nine enumerated phrasings raised
    `ambiguous` and aborted the phase with zero artifacts. Only `linear lags`, `linear lag`,
    `linear regression`, `linear model` are aliased.
  - **Bagging is not aliased to boosting.** `random forest`/`extra trees`/`decision tree`/
    `tree ensemble` previously resolved to `gradient_boosting_lags`, and a coherent RandomForest
    design (`bootstrap`, `oob_score`, a bagging rationale) validated and was written as a boosting
    family with its RF hyperparameters intact — `coder` would build a boosting model, be handed
    `bootstrap=`, and die on a constructor `TypeError` from a design contradicting its own
    `rationale`. Now it raises "not a supported model family": loud and recoverable. Same principle
    `deep_learning_specialist` already documents — rejecting is the safe direction to fail.
  - **Concatenated/CamelCase spellings are listed explicitly**, because normalization collapses
    `-`/`_` to a space but never splits CamelCase. `ExponentialSmoothing` — statsmodels' own class
    name for one of these five families — was a hard abort, as were `HoltWinters`,
    `GradientBoostingRegressor`, `HistGradientBoostingRegressor`, `XGBRegressor`, `LGBMRegressor`
    and the CamelCase rendering of this table's own tokens.
  - *Not* aliased on purpose: the selector's routing vocabulary ("forecast", "time series") and a
    bare "lag features" — both lag families are models over lag features, so the model brand alone
    discriminates.
- *No self-gate on temporal structure.* "Activated only when temporal structure exists" is satisfied
  upstream — `specialist_selector` (T-023) is the sole gate and nothing is queued behind this node,
  so a refusal branch would leave the iteration with zero artifacts. Asserted by a test, not just
  documented.
- *"Respect temporal CV / no future leakage" split into an enforced half and a prompt-level half*
  (user-approved reinterpretation). Enforced and tested: pipeline-injected `cv_strategy_ref` plus
  `FORBIDDEN_CV_KEYS` rejection, including the `TimeSeriesSplit`-shaped keys. Prompt-level only:
  "never uses future data" — fit scope is not expressible in `design.json` (`preprocessing` is a flat
  token list; `FORBIDDEN_CV_KEYS` matches keys, not list values), and enforcing it would mean editing
  the shared contract T-024–T-028 all inherit. **No leakage detection was added to the validator.**
  Related: the frozen strategy may legitimately not be time-aware; the folds are write-once, so the
  node designs against them and notes the mismatch in `rationale`.
- *Column identity comes from `feature_spec_ref`.* `_FOLD_SUMMARY_KEYS`/`read_fold_summary`
  deliberately not widened to carry a time column — it would stale three landed sibling prompts.
- *Tuple-shaped ARIMA `order`/`seasonal_order` as `p-d-q` string tokens*, following T-026's
  `ngram_range` precedent. Scope corrected during review: the array ban is *enforced* only inside
  `search_space` (`choices` takes scalars); `fixed_params` accepts a flat list, so the ban there is
  stated as a pipeline convention, not a validator rejection. Handed to T-029 in discoveries, along
  with the fact that the string convention itself is unvalidated.

**Handed to T-028/T-029 via `context/discoveries.md`:** the terminated re-pointing chain; the
unvalidated `p-d-q` convention and the two encodings that can both reach `design.json`; the six
fold-shaping keys the prompt forbids but `FORBIDDEN_CV_KEYS` does not reject (a silent
score-comparability hazard against invariants #1/#3); `gradient_boosting_lags` being coarser than
`classical_ml_specialist`'s families; and the now-partly-inaccurate `normalize_model_family`
docstring example.
