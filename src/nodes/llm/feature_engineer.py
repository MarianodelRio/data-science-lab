"""feature_engineer: reads the solution plan + EDA report and designs feature
transformations, writing design/iteration_{iteration}/feature_spec.json.

Schema v2 (T-047) replaced v1's three fixed categories (`encodings`,
`null_handling`, `interactions`) with **one open-vocabulary primitive**: a
single `features` list whose every entry is
`{columns, operation, params, fit_scope, rationale}`. A per-column transform
and a multi-column interaction are the same shape — one entry with one column
or with many — and `operation` is a free string, so cyclical encoding, log/power
transforms, datetime-part extraction, aggregations, text length and outlier
clipping all have somewhere to go, which they did not under v1.

`fit_scope` is required on every entry, has no default, and is exactly
`"per_fold"` or `"global"`. It generalizes v1's target-encoding-only
`fold_aware: true` boolean: train-fit scope is now an explicit property of
every transformation rather than one family's special case, and six leakage-prone
families (not just target encoding) are forced to `per_fold` by
`_matched_fit_scope_family`.

Overrides `_build_messages` (inject the solution plan and EDA report as an
extra HumanMessage), `_write_output` (extract + validate the JSON payload,
write it via `workspace.write_json`), AND `_build_output_state` — unlike
`baseline_designer`, this node DOES set a new `LabState` field
(`feature_spec_path`): it is load-bearing for
`analysis_critic._detect_phase_stem`, which distinguishes a Phase 1
(Understanding) critic pass from a Phase 4 (Design) critic pass by checking
whether `feature_spec_path` has been written yet.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.nodes.llm._experiment_design import DEGRADE_ERRORS, is_json_scalar
from src.nodes.llm.base import LLMNode, relative_to_workspace
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager


def _strip_outer_fence(content: str) -> str:
    """Strip a single outer fence wrapping the entire response, if present.

    Same outer-fence-anchoring approach as `baseline_designer._strip_outer_fence`/
    `problem_framer._strip_outer_fence`/`leakage_auditor._strip_outer_fence`: anchors
    on the outermost ``` markers only, so an embedded ``` inside a string value (e.g.
    a hyperparameter description quoting code) is never mistaken for the closing fence.
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    if not text.endswith("```") or len(text) < 6:
        raise ValueError("feature_engineer response starts with a fence but never closes it")
    first_newline = text.find("\n")
    if first_newline == -1:
        raise ValueError("feature_engineer response fence has no content")
    inner = text[first_newline + 1 :]
    closing_idx = inner.rfind("```")
    if closing_idx == -1:
        raise ValueError("feature_engineer response fence has no closing delimiter")
    return inner[:closing_idx].strip()


def _extract_json(content: str) -> dict[str, Any]:
    """Extract a JSON object from the LLM response.

    Accepts: raw JSON with no fence, or the entire response wrapped in a
    single ```json or unlabeled ``` fence. Invalid JSON raises a clear
    ValueError naming 'feature_engineer'.
    """
    text = _strip_outer_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"feature_engineer response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"feature_engineer response must be a JSON object, got {type(data).__name__}"
        )
    return data


def _read_solution_plan(state: LabState, workspace: WorkspaceManager) -> str:
    """Read state['solution_plan_path'] as pretty-printed JSON text. Degrades
    to a placeholder, never raises — mirrors baseline_designer._read_problem_definition.
    T-021 (solution_architect) may not have run yet, so this path is legitimately
    unset during standalone/partial-phase execution.

    Still a private per-module copy (the T-022 decision that these reader helpers
    are duplicated per module, with only `relative_to_workspace` hoisted in T-020,
    is unchanged). Only the caught set changed in T-047: it is now
    `_experiment_design.DEGRADE_ERRORS`, which carries the rationale for the exact
    tuple, rather than a bare `OSError` that let a truncated file
    (`json.JSONDecodeError`), a non-UTF-8 byte (`UnicodeDecodeError`), a `..`/moved
    path (`ValueError` out of `WorkspaceManager._resolve`) or a pathological nesting
    depth (`RecursionError`) escape a helper that promises never to raise (the T-024
    discovery). `json.dumps` is inside the `try` for the same reason: a payload deep
    enough to recurse on the way in recurses again on the way out. A non-`str` path
    is guarded explicitly — `LabState` types it as `str`, but LangGraph does not
    enforce the TypedDict at runtime, and `Path()` would raise `TypeError`, which is
    deliberately *not* in the caught set.
    """
    path = state.get("solution_plan_path") or ""
    if not isinstance(path, str) or not path:
        return "(solution plan not yet available)"
    try:
        data = workspace.read_json(relative_to_workspace(path, workspace))
        return json.dumps(data, indent=2)
    except DEGRADE_ERRORS:
        return f"(unable to read solution plan at {path})"


def _read_eda_report(state: LabState, workspace: WorkspaceManager) -> str:
    """Same behavior as baseline_designer._read_eda_report — own copy,
    per-module duplication is the established convention for these reader
    helpers (only relative_to_workspace itself was hoisted, in T-020).

    Catches `_experiment_design.DEGRADE_ERRORS` and guards a non-`str` path for the
    same reasons spelled out on `_read_solution_plan` above (T-047 / the T-024
    discovery). Nothing is re-serialized here — `read_text` already returns a
    `str` — so there is no second recursion hazard to guard.
    """
    path = state.get("eda_report_path") or ""
    if not isinstance(path, str) or not path:
        return "(EDA report not yet available)"
    try:
        return workspace.read_text(relative_to_workspace(path, workspace))
    except DEGRADE_ERRORS:
        return f"(unable to read EDA report at {path})"


# Curated keyword sets for the transformation families whose `fit_scope` the validator
# constrains. Every one of these families is *fitted*: it learns a parameter, statistic,
# mapping, vocabulary or basis from the data it is applied to, so fitting it once over the
# whole training set carries information out of every held-out fold frozen in
# `validation/fold_config.json` and inflates the CV score. Each therefore requires
# `"fit_scope": "per_fold"`.
#
# Matching is by whole phrase with word boundaries against a normalized copy of the operation
# string — `-`/`_` collapsed to spaces, case folded, and camelCase split — so `standard_scale`,
# `standard-scale`, `Standard Scale` and `StandardScaler` all match the same keyword uniformly.
# That mechanism is T-022's, generalized in T-047 from one family to six and extended in the
# T-047 review round to camelCase, because sklearn's own class names (`TargetEncoder`,
# `MinMaxScaler`, `KNNImputer`) are probable `operation` values and were reachable by no keyword.
#
# Deliberately absent from every tuple: the bare stems `scale`, `transform`, `normalize`,
# `normalization`, `standardize`, `standardization`, `encoding`, `mean`, `count` and `impute`.
# Each appears inside operations that are legitimately stateless (`log_transform`,
# `text_normalization`, `standardize_country_codes`, `count_distinct_categories`,
# `mean_of_last_3_orders`), and adding one would flag them. `standardize`/`standardization` were
# added to this list in the second T-047 review round after being briefly present in
# `_SCALER_KEYWORDS`: they are direct synonyms of `normalize`/`normalization` and mean "make
# uniform" far more often than "z-score" (`standardize_address_format`, `standardize_text_case`,
# `StandardizePhoneNumber` all matched). Genuine z-scoring stays covered by `standard scale(r)`,
# `zscore`/`z score`.
# Because `\b...\b` matching makes `impute` no prefix of `imputation`, both stems of each
# word are listed explicitly — do not collapse the variants.
#
# The families are not equally severe. Target encoding is **target** leakage: the encoding
# value for a row is derived from the target column, so a CV score computed with it is not
# merely optimistic, it is measuring the target. The other five leak only *feature*
# statistics from the held-out fold (a scaler's mean/σ, an imputer's median, a PCA basis, a
# category's global frequency), which inflates the score more mildly. Requiring `per_fold`
# for both is a deliberate conservative stance, not a claim that they are the same bug.
#
# That conservatism is **not symmetric**, and the earlier wording here ("cheap, because forcing
# `per_fold` on a stateless operation produces identical output") was wrong about why. This guard
# does not coerce `fit_scope`; it raises `ValueError`, and `LLMNode.__call__`
# (`src/nodes/llm/base.py`) does not catch it and no node-level handler in `src/graph/` does
# either. So a false positive is not a harmlessly-conservative `per_fold` — it aborts the Phase 4
# run on a *correct* response. A false negative, by contrast, has three layers behind it: the
# prompt's general "anything fitted is `per_fold`" rule, `code_critic`'s leakage rubric, and the
# remaining keywords of the same family. Under-matching is a covered silent leak; over-matching is
# a dead run with nothing behind it. That asymmetry — not a balanced trade-off — is why bare stems
# are strictly bad, and why a keyword is added only when its phrase is unambiguously the fitted
# technique and nothing else.
_TARGET_ENCODING_KEYWORDS = (
    "target encoding",
    "target encode",
    "target encoder",
    "target mean",
    "smoothed target",
    "mean encoding",
    "leave one out",
    "loo",
    "woe",
    "weight of evidence",
    "catboost",
    # `category_encoders`' real class name is `CatBoostEncoder`, which the camelCase split turns
    # into `cat boost encoder` — reachable by no concatenated keyword. `catboostencoder` (an
    # unseparated lowercase run) still matches nothing; that is the documented non-coverage.
    "cat boost",
    "james stein",
    "m estimate",
    "impact encoding",
)

_STATISTICAL_IMPUTATION_KEYWORDS = (
    "median impute",
    "median imputation",
    "median imputer",
    "mean impute",
    "mean imputation",
    "mean imputer",
    "mode impute",
    "mode imputation",
    "mode imputer",
    "most frequent impute",
    "most frequent imputation",
    "most frequent imputer",
    "knn impute",
    "knn imputation",
    "knn imputer",
    "iterative impute",
    "iterative imputation",
    "iterative imputer",
    "simple imputer",
    "simple impute",
    # Verb-first and pandas phrasings. `fillna_median` is the single most probable name an
    # LLM emits for this transformation and matched nothing before the T-047 review.
    "impute median",
    "impute mean",
    "impute mode",
    "fillna median",
    "fillna mean",
    "fillna mode",
    "median fill",
    "mean fill",
    "mode fill",
    "mice",
)

_SCALER_KEYWORDS = (
    "standard scale",
    "standard scaler",
    "standard scaling",
    "min max scale",
    "min max scaler",
    "min max scaling",
    "minmax",
    "robust scale",
    "robust scaler",
    "robust scaling",
    "max abs scale",
    "max abs scaler",
    "max abs scaling",
    "maxabs",
    "z score",
    "zscore",
    "quantile transform",
    "quantile transformer",
    "power transform",
    "power transformer",
    "yeo johnson",
    "box cox",
    # Deliberately NOT here: "unit norm" and "l2 normalize". sklearn's `Normalizer` scales each
    # sample by its own norm, so it learns nothing from any other row — it is stateless and
    # row-wise, exactly what the prompt tells the LLM to declare `global` for. Flagging it made a
    # *correct* declaration raise, and `LLMNode.__call__` does not catch `ValueError`, so that
    # aborted the Phase 4 run. The concatenated spellings ("zscore", "minmax", "maxabs") above are
    # the shapes that were genuinely missing.
    #
    # Also deliberately NOT here, and removed in the second T-047 review round: "standardize" and
    # "standardization". They are bare stems in the sense of the block comment above — synonyms of
    # `normalize`/`normalization` whose everyday meaning is "make uniform", so they matched
    # `standardize_address_format`, `standardize_text_case`, `standardize_country_codes`,
    # `StandardizePhoneNumber` and `standardize_units_to_metric`, every one of which is stateless
    # and every one of which aborted the run. z-scoring remains covered by "standard scale(r)",
    # "z score" and "zscore".
)

_BINNING_KEYWORDS = (
    "quantile bin",
    "quantile bucket",
    "decile bin",
    "kbins",
    "k bins",
    "equal frequency bin",
    "equal width bin",
    "binning",
    "discretize",
    "discretizer",
    "discretization",
)

_DIMENSIONALITY_REDUCTION_KEYWORDS = (
    "pca",
    "principal component",
    "svd",
    "truncated svd",
    "umap",
    "tsne",
    "t sne",
    "nmf",
    "non negative matrix factorization",
    "latent dirichlet",
    "linear discriminant",
)

_FREQUENCY_ENCODING_KEYWORDS = (
    "frequency encoding",
    "frequency encode",
    "count encoding",
    "count encode",
    "value counts encoding",
)

# (family label, keywords). The label is only ever used in the error message, so it reads
# as prose, not as a token.
_FIT_SCOPE_SENSITIVE_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("target encoding", _TARGET_ENCODING_KEYWORDS),
    ("statistical imputation", _STATISTICAL_IMPUTATION_KEYWORDS),
    ("scaling / normalization", _SCALER_KEYWORDS),
    ("binning / discretization", _BINNING_KEYWORDS),
    ("dimensionality reduction", _DIMENSIONALITY_REDUCTION_KEYWORDS),
    ("frequency / count encoding", _FREQUENCY_ENCODING_KEYWORDS),
)

_FEATURE_KEYS = ("columns", "operation", "params", "fit_scope", "rationale")
_FIT_SCOPES = ("per_fold", "global")


# camelCase boundaries, split before lowercasing. `(?<=[a-z0-9])(?=[A-Z])` catches the ordinary
# word boundary (`TargetEncoder` -> `Target Encoder`, `MinMaxScaler` -> `Min Max Scaler`);
# `(?<=[A-Z])(?=[A-Z][a-z])` catches an acronym followed by a capitalized word (`KNNImputer` ->
# `KNN Imputer`, not `K N N Imputer`). A bare all-caps token has no boundary of either kind, so
# `PCA` and `WOE` normalize to `pca`/`woe` and keep matching as before.
_CAMEL_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CAMEL_ACRONYM_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")


def _collapse_separators(value: str) -> str:
    """Lowercase copy with `-`/`_` runs and whitespace collapsed to single spaces — the exact
    normalization T-022's `_is_target_encoding_method` used, unchanged."""
    normalized = re.sub(r"[-_]+", " ", value.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_operation(value: str) -> str:
    """`_collapse_separators` plus a camelCase split, so sklearn's own class names reach the
    keyword tuples: `TargetEncoder` -> `target encoder`, `MinMaxScaler` -> `min max scaler`,
    `KNNImputer` -> `knn imputer`, while `PCA` -> `pca` and `WOE` -> `woe` are untouched.

    Not covered, and stated rather than silently assumed: an all-lowercase concatenation with no
    separator and no case boundary (`targetencoder`, `medianimpute`) cannot be split by any rule
    that does not guess word breaks, so it matches nothing. Neither does a name whose only
    boundary is a digit run (`Top10Encoder` -> `top10 encoder`, since `[a-z0-9]` deliberately
    treats digits as part of the preceding word rather than splitting `top 10 encoder`).
    """
    spaced = _CAMEL_ACRONYM_BOUNDARY.sub(" ", _CAMEL_WORD_BOUNDARY.sub(" ", value))
    return _collapse_separators(spaced)


def _normalized_operation_variants(value: str) -> tuple[str, ...]:
    """Both readings of an operation string, because neither alone is sufficient.

    Splitting camelCase is what makes `TargetEncoder` reachable, but it also breaks apart names
    the tuples carry *concatenated*: `CatBoost encoding` splits to `cat boost encoding`, which no
    longer matches the `catboost` keyword. Matching against both forms therefore **loses
    nothing** — that much is provable from the construction, since the returned tuple always
    contains the plain separator-collapsed form, which *is* the pre-camelCase normalization, so
    the match set is a strict superset of the old one.

    What does *not* follow, and was wrongly claimed here before the second T-047 review round, is
    that it cannot add a false positive. It matches strictly more strings — that is its purpose —
    so it can surface one the unsplit form did not have: `StandardizeTextCase`, `ModeFillColorFlag`,
    `ImputeMeanFlagOnly`, `FillnaMeanIndicator` and `TargetEncoderFreeBaseline` all match split and
    none match unsplit. Every such case found so far has a snake_case twin that false-positives
    under both forms, so the root cause is the keyword, not the splitting. The mitigation is
    keyword discipline — no bare stems, see the family-constant block — not any property of this
    function. Do not read it as licence to add keywords freely.
    """
    split = _normalize_operation(value)
    joined = _collapse_separators(value)
    return (split,) if split == joined else (split, joined)


def _matched_fit_scope_family(operation: str) -> str | None:
    """The label of the first leakage-prone family whose keyword set matches
    `operation` as a whole phrase (`\\b...\\b`), against either normalization variant, or `None`.

    Normalization collapses `-`/`_` separators and case and splits camelCase, so `standard_scale`,
    `standard-scale`, `Standard Scale` and `StandardScaler` all reach the same keywords. It does
    **not** split an unseparated all-lowercase run (`targetencoder`), which therefore matches
    nothing — see `_normalize_operation`.

    `None` is the documented residual risk, not a claim of safety: `operation` is an
    open vocabulary, so a genuinely novel fitted technique (`groupby_user_mean_amount`)
    matches nothing here and may declare `fit_scope: "global"`. Same honest-scope
    framing as `FORBIDDEN_CV_KEYS` in `_experiment_design.py`; `code_critic`'s leakage
    rubric is the downstream net, and `config/prompts/feature_engineer/v2.md` carries
    the general "anything fitted is per_fold" rule that governs everything above this
    keyword floor.
    """
    candidates = _normalized_operation_variants(operation)
    for family, keywords in _FIT_SCOPE_SENSITIVE_FAMILIES:
        if any(
            re.search(rf"\b{re.escape(keyword)}\b", candidate)
            for candidate in candidates
            for keyword in keywords
        ):
            return family
    return None


def _validate_columns(index: int, value: Any) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(c, str) and c.strip() for c in value)
    ):
        raise ValueError(
            f"feature_engineer response 'features[{index}]' field 'columns' must be a "
            f"non-empty list of non-empty strings, got {value!r}"
        )
    if len(set(value)) != len(value):
        raise ValueError(
            f"feature_engineer response 'features[{index}]' field 'columns' names the same "
            f"column more than once, got {value!r} — an operation applied to a column and "
            f"itself (a ratio, a difference, an interaction) yields a constant, which reads "
            f"downstream as a modeling problem rather than as the specification bug it is. "
            f"`solution_architect` rejects duplicate 'model_families' for the same reason"
        )
    return list(value)


def _validate_text_field(index: int, field: str, value: Any) -> str:
    """Returns the **stripped** value. `"  target_encoding  "` passes the truthiness check but
    `coder` (T-029) string-matches `operation`, and the family guard matches on word boundaries,
    so padding must never reach the artifact. Applied to `rationale` too, for consistency."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"feature_engineer response 'features[{index}]' missing required non-empty "
            f"string field {field!r}"
        )
    return value.strip()


def _validate_params(index: int, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(
            f"feature_engineer response 'features[{index}]' field 'params' must be an "
            f"object (use an empty object when there are none), got {value!r}"
        )
    validated: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(
                f"feature_engineer response 'features[{index}]' field 'params' has an "
                f"empty or non-string key {key!r}"
            )
        if not is_json_scalar(item) and not (
            isinstance(item, list) and all(is_json_scalar(entry) for entry in item)
        ):
            raise ValueError(
                f"feature_engineer response 'features[{index}]' entry 'params.{key}' must "
                f"be a finite JSON scalar or a flat list of them — a nested object cannot "
                f"be applied to a column, and a non-finite number or an integer beyond "
                f"±2**53 is not portable JSON, got {item!r}"
            )
        validated[key] = list(item) if isinstance(item, list) else item
    return validated


def _validate_fit_scope(index: int, operation: str, columns: list[str], value: Any) -> str:
    """Exact-token match against `_FIT_SCOPES`, then the family guard.

    `value not in _FIT_SCOPES` rejects a missing key (`.get` returns `None`), a boolean,
    `"per-fold"` and `"PER_FOLD"` in one branch — matching is deliberately
    neither case-folded nor separator-normalized, unlike the *operation* matching above:
    `fit_scope` is a machine-read enum that `coder` (T-029) branches on, so exactly two
    spellings may reach the artifact.
    """
    if value not in _FIT_SCOPES:
        raise ValueError(
            f"feature_engineer response 'features[{index}]' field 'fit_scope' must be "
            f"exactly one of {list(_FIT_SCOPES)} — it is required on every entry and has "
            f"no default, got {value!r}"
        )
    family = _matched_fit_scope_family(operation)
    if family is not None and value != "per_fold":
        raise ValueError(
            f"feature_engineer response 'features[{index}]' operation {operation!r} on "
            f"columns {columns!r} is a {family} technique — it is fitted on data, so it "
            f"must declare 'fit_scope': 'per_fold' (fitting it over the whole training set "
            f"leaks held-out-fold information into every fold frozen in "
            f"validation/fold_config.json), got {value!r}"
        )
    return str(value)


def _validate_feature_entry(index: int, item: Any) -> dict[str, Any]:
    """Whitelist rebuild: returns a fresh dict with exactly `_FEATURE_KEYS`, in that
    order. Every other key the LLM sent — including v1's `fold_aware`, `column`,
    `method`, `strategy` and `type` — is dropped, and the LLM's own object is never
    written through.

    Field order is load-bearing: `operation` is validated *before* `fit_scope`, because
    the family guard needs a validated string. A blank `operation` therefore raises the
    `operation` message, not the `fit_scope` one.
    """
    if not isinstance(item, dict):
        raise ValueError(
            f"feature_engineer response 'features[{index}]' must be an object, got {item!r}"
        )
    columns = _validate_columns(index, item.get("columns"))
    operation = _validate_text_field(index, "operation", item.get("operation"))
    return {
        "columns": columns,
        "operation": operation,
        "params": _validate_params(index, item.get("params")),
        "fit_scope": _validate_fit_scope(index, operation, columns, item.get("fit_scope")),
        "rationale": _validate_text_field(index, "rationale", item.get("rationale")),
    }


def _validate_features(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(
            f"feature_engineer response missing required list field 'features', got {value!r}"
        )
    entries = [_validate_feature_entry(i, item) for i, item in enumerate(value)]
    first_seen: dict[tuple[tuple[str, ...], str], int] = {}
    for index, entry in enumerate(entries):
        key = (tuple(entry["columns"]), entry["operation"])
        previous = first_seen.get(key)
        if previous is not None:
            raise ValueError(
                f"feature_engineer response 'features[{index}]' repeats 'features[{previous}]': "
                f"the same operation {entry['operation']!r} on the same columns "
                f"{entry['columns']!r}. `coder` derives one column per entry, so the pair is a "
                f"name collision — identical 'params' or not, only one of the two can survive"
            )
        first_seen[key] = index
    return entries


def _validate_feature_spec(data: dict[str, Any]) -> dict[str, Any]:
    return {"features": _validate_features(data.get("features"))}


class FeatureEngineerNode(LLMNode):
    name = "feature_engineer"

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        solution_plan = _read_solution_plan(state, workspace)
        eda_report = _read_eda_report(state, workspace)
        messages.append(
            HumanMessage(
                content=(f"## Solution plan\n\n{solution_plan}\n\n## EDA report\n\n{eda_report}")
            )
        )
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        data = _extract_json(content)
        validated = _validate_feature_spec(data)
        return workspace.write_json(relative_path, validated)

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        return {"feature_spec_path": written_path}
