"""timeseries_specialist: reads the solution plan and the frozen fold summary and
designs one temporal/forecasting experiment — model family, Optuna search space,
fixed params, model-specific temporal preprocessing (lag/rolling/seasonal
features) — writing experiments/exp_{iteration}/design.json.

The `design.json` schema itself lives in `src/nodes/llm/_experiment_design.py`,
shared with the other Pipeline Phase 5 specialists and their `coder` consumer.

Overrides `_build_messages` (inject the solution plan, the frozen fold summary
and the feature-spec reference as an extra HumanMessage) and `_write_output`
(extract + validate the JSON payload, write it via `workspace.write_json`).
Does NOT override `_build_output_state` — `coder` (T-029) reads
`experiments/exp_{iteration}/design.json` from its well-known path directly, the
same convention `baseline_designer`/`fold_config.json` already use, so no new
`LabState` field is needed (`src/state.py` is a protected contract).

This node never self-gates on whether temporal structure exists:
`specialist_selector` (T-023) is the only gate, and by the time this node runs
the routing decision has already been made. "Activated only when temporal
structure exists" is therefore satisfied upstream, not re-checked here.

Frozen folds, honestly scoped. The enforceable half of "respect temporal CV" is
shared machinery: `cv_strategy_ref` is pipeline-injected (never read from the
response) and `FORBIDDEN_CV_KEYS` rejects any attempt to redefine CV. The other
half — "never uses future data" — is **prompt-level guidance only**, deliberately
not validator-enforced: fit scope is not expressible in `design.json` (a flat
list of step names), and adding leakage detection would mean editing the shared
contract T-024–T-028 all inherit. The prompt therefore pushes the requirement
into the token itself (`rolling_mean_past_only` over a bare `rolling_mean`). The
frozen strategy may also legitimately not be time-aware (`stratified_kfold`);
that is a read-only fact of the run and this node may not change it.

Column identity (time column, date column, target column) comes from
`feature_spec_ref`, which this node passes through as a path. `_FOLD_SUMMARY_KEYS`
is deliberately NOT widened to carry a time column — doing so would stale the
three landed sibling prompts — and there is no node-local fold reader here.
"""

from __future__ import annotations

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm._experiment_design import (
    extract_json_object,
    read_fold_summary,
    read_solution_plan,
    resolve_feature_spec_ref,
    validate_experiment_design,
)
from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

_SPECIALIST = "timeseries_specialist"

# Canonical model family -> phrases that denote it. Matched as whole phrases with
# word boundaries against a separator-normalized copy of the LLM's `model_family`
# string (see `_experiment_design.normalize_model_family`). Human checkpoint
# decision: exactly five canonical families.
#
# Aliasing is deliberately generous. `specialist_selector`'s routing vocabulary
# ("time series forecasting", "forecast", "arima", "prophet") is almost DISJOINT
# from this token set, and these nodes have no retry wrapper — an unresolvable
# `model_family` is a hard `ValueError` that aborts the phase with zero artifacts
# (see the 2026-08-12 T-025 entry in context/discoveries.md).
#
# Two spelling axes are handled here rather than in `normalize_model_family`,
# which has no longest-match-wins rule (2026-08-12 T-026 discovery) and is NOT
# changed by this task:
#   1. Concatenated/CamelCase spellings. Normalization collapses `-`/`_` to a
#      space but never splits CamelCase, so a one-word spelling is unreachable
#      from its spaced alias: `\barima\b` does not match inside "autoarima" or
#      "sarimax", and `\bgradientboosting\b` does not match inside
#      "gradientboostingregressor". This matters most for the library class names
#      an LLM actually writes — `ExponentialSmoothing` is statsmodels' own class
#      name for one of these five families, and `XGBRegressor`/`LGBMRegressor`/
#      `GradientBoostingRegressor` are the sklearn-API spellings — and for the
#      CamelCase rendering of this table's own canonical tokens
#      (`ExponentialSmoothing`, `GradientBoostingLags`, `LinearLags`). Every
#      concatenated form is therefore listed alongside its spaced twin, following
#      the convention already used for `auto arima`/`autoarima`,
#      `fb prophet`/`fbprophet`, `light gbm`/`lightgbm`, `elastic net`/
#      `elasticnet`.
#   2. The lag-feature modifier is NOT aliased anywhere. `gradient_boosting_lags`
#      and `linear_lags` are BOTH "model over lag features", so a bare "lag
#      features" cannot be assigned to either; listing it under one would make
#      "ridge over lag features" match two families and raise "ambiguous" on a
#      perfectly clear answer, and listing it under both would do the same for
#      every phrase. The discriminator is therefore the model brand alone:
#      "lag features for XGBoost" and "XGBoost" both resolve via "xgboost".
#
# Deliberately NOT aliased, in both directions:
#   - Generic routing words ("forecast", "time series"). Mapping them to a family
#     is an arbitrary modeling decision, and they co-occur with real family names
#     constantly ("ARIMA for time series forecasting"), which would turn a clear
#     answer into an "ambiguous" rejection. The prompt pins the five bare literal
#     tokens instead.
#   - A bare "linear". It is a *trend* word far more often than a family word in
#     this domain, and every use of it co-occurs with another family: "Holt's
#     linear trend method" (the textbook name), "damped linear trend exponential
#     smoothing", "Prophet (growth=linear)" (Prophet's own default),
#     "ARIMA with linear trend", "LightGBM linear_tree", "gradient boosting with
#     linear base learners". Aliasing it made all of those raise "ambiguous" and
#     abort the phase with zero artifacts. Only the qualified forms
#     ("linear lags", "linear regression", "linear model") are aliased.
_MODEL_FAMILIES: dict[str, tuple[str, ...]] = {
    "arima": (
        "arima",
        "arimax",
        "sarima",
        "sarimax",
        "seasonal arima",
        "auto arima",
        "autoarima",
        "arma",
        "ar",
        "box jenkins",
    ),
    "prophet": (
        "prophet",
        "fbprophet",
        "fb prophet",
        "neuralprophet",
        "neural prophet",
    ),
    "exponential_smoothing": (
        "exponential smoothing",
        "exponentialsmoothing",
        "ets",
        "holt winters",
        "holtwinters",
        "holt",
        "ses",
    ),
    # BOOSTING ONLY — bagging is deliberately absent. "random forest", "extra
    # trees", "decision tree" and "tree ensemble" were removed after an
    # adversarial review demonstrated the failure end to end: a coherent
    # RandomForest design (`bootstrap: true`, `oob_score: false`, and a rationale
    # arguing for bagging) validated and was written as
    # `model_family: "gradient_boosting_lags"` with its RF hyperparameters intact,
    # so `coder` (T-029) would dispatch to a boosting model, be handed
    # `bootstrap=`/`oob_score=`, and die on a constructor `TypeError` — from a
    # `design.json` that contradicts its own `rationale`. Unaliased, a bagged-tree
    # answer instead raises "not a supported model family": loud, attributable and
    # recoverable. Same principle `deep_learning_specialist.py:60-73` states
    # verbatim — rejecting is the safe direction to fail, silently resolving to
    # the wrong family is not.
    "gradient_boosting_lags": (
        "gradient boosting lags",
        "gradientboostinglags",
        "gradient boosting",
        "gradientboosting",
        "gradientboostingregressor",
        "gradient boosted trees",
        "gradient boosted regression trees",
        "boosted trees",
        "gbm",
        "gbdt",
        "gbrt",
        "xgboost",
        "xgb",
        "xgbregressor",
        "lightgbm",
        "light gbm",
        "lgbm",
        "lgb",
        "lgbmregressor",
        "catboost",
        "hist gradient boosting",
        "histgradientboosting",
        "histgradientboostingregressor",
    ),
    "linear_lags": (
        "linear lags",
        "linearlags",
        "linear lag",
        "linear regression",
        "linearregression",
        "linear model",
        "ridge",
        "lasso",
        "elastic net",
        "elasticnet",
        "ols",
        "least squares",
        "leastsquares",
    ),
}


class TimeseriesSpecialistNode(LLMNode):
    name = "timeseries_specialist"

    # Resolved in `_build_messages` and consumed in `_write_output`:
    # `LLMNode.__call__` never passes `state` to `_write_output`, so the
    # workspace-relative feature-spec pointer has to be stashed between the two
    # (same mechanism as `classical_ml_specialist`/`literature_researcher`).
    _feature_spec_ref: str = ""

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        self._feature_spec_ref = resolve_feature_spec_ref(state, workspace)
        solution_plan = read_solution_plan(state, workspace)
        fold_summary = read_fold_summary(state, workspace)
        messages.append(
            HumanMessage(
                content=(
                    f"## Solution plan\n\n{solution_plan}\n\n"
                    f"## Frozen CV folds\n\n{fold_summary}\n\n"
                    f"## Feature spec reference\n\n{self._feature_spec_ref}"
                )
            )
        )
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        if not self._feature_spec_ref:
            raise ValueError(
                f"{_SPECIALIST}._write_output was called before _build_messages resolved the "
                "feature spec reference; refusing to write a design with an unknown "
                "'feature_spec_ref'"
            )
        content = response.content if isinstance(response.content, str) else str(response.content)
        data = extract_json_object(content, _SPECIALIST)
        validated = validate_experiment_design(
            data,
            specialist=_SPECIALIST,
            allowed_families=_MODEL_FAMILIES,
            feature_spec_ref=self._feature_spec_ref,
        )
        return workspace.write_json(relative_path, validated)
