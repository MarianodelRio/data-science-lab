"""Shared `design.json` contract for the Pipeline Phase 5 specialist nodes —
`classical_ml_specialist` (T-024) and, as they land, `deep_learning_specialist`
(T-025), `nlp_specialist` (T-026), `timeseries_specialist` (T-027),
`ensemble_specialist` (T-028) — plus their downstream consumer `coder` (T-029),
which reads the file this module shapes.

This module declares no class matching its own filename stem
(`_experiment_design`), so `src/graph/node_resolver.py`'s `_find_node_class`
never mistakes it for a node module — see docs/pipeline.md § Node-module
convention. It is imported by the specialist node modules above but never
referenced in `config/phases/*.yaml`.

Every public function takes the calling node's `specialist` name so error
messages attribute the bad response to the specialist that produced it —
the same `node_name`-parameter convention `_research_common.py` uses.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from src.nodes.llm.base import relative_to_workspace
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

# `validation/fold_config.json` is write-once (CLAUDE.md invariant #1) and always
# lives at this fixed workspace-relative path — never trust the LLM to name it
# correctly, inject it ourselves (same convention as `baseline_designer`).
CV_STRATEGY_REF = "validation/fold_config.json"

# Where `feature_engineer` (T-022) writes `feature_spec.json` when
# `state["feature_spec_path"]` has not been populated yet (Phase 5 exercised
# standalone, ahead of a real Phase 4 run).
FEATURE_SPEC_FALLBACK_PATTERN = "design/iteration_{iteration}/feature_spec.json"

# The exact top-level key set of `experiments/exp_{iteration}/design.json`, in
# written order. `validate_experiment_design` rebuilds a fresh dict with exactly
# these keys — the LLM's own object is never written through.
DESIGN_KEYS = (
    "specialist",
    "model_family",
    "search_space",
    "fixed_params",
    "preprocessing",
    "rationale",
    "feature_spec_ref",
    "cv_strategy_ref",
)

# The Optuna parameter kinds a `search_space` entry may declare.
PARAM_TYPES = ("int", "float", "categorical")

# Cross-validation is frozen in `validation/fold_config.json` before Phase 5 ever
# runs, so a specialist that emits any of these is trying to redefine CV. Matched
# by *exact key name* (never substring), which is why `cv_strategy_ref` — the
# pipeline-injected pointer to the frozen folds — is deliberately absent from this
# set: whatever the LLM sends under that key is simply ignored and re-injected.
FORBIDDEN_CV_KEYS = frozenset(
    {
        "cv",
        "cv_strategy",
        "folds",
        "fold_indices",
        "n_folds",
        "n_splits",
        "validation",
        "test_size",
        "shuffle",
    }
)

_FOLD_SUMMARY_KEYS = ("strategy", "n_folds", "seed")
_FOLDS_NOT_AVAILABLE = "(frozen fold config not yet available)"


def strip_outer_fence(content: str, specialist: str) -> str:
    """Strip a single outer fence wrapping the entire response, if present.

    Same outer-fence-anchoring approach as `_research_common._strip_outer_fence`/
    `baseline_designer._strip_outer_fence`, parameterized by `specialist` rather
    than copied a fourth time: anchors on the outermost ``` markers only, so an
    embedded ``` inside a string value (e.g. a rationale quoting code) is never
    mistaken for the closing fence.
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    if not text.endswith("```") or len(text) < 6:
        raise ValueError(f"{specialist} response starts with a fence but never closes it")
    first_newline = text.find("\n")
    if first_newline == -1:
        raise ValueError(f"{specialist} response fence has no content")
    inner = text[first_newline + 1 :]
    closing_idx = inner.rfind("```")
    if closing_idx == -1:
        raise ValueError(f"{specialist} response fence has no closing delimiter")
    return inner[:closing_idx].strip()


def extract_json_object(content: str, specialist: str) -> dict[str, Any]:
    """Extract a top-level JSON object from an LLM response.

    Accepts raw JSON with no fence, or the entire response wrapped in a single
    ```json or unlabeled ``` fence. Raises `ValueError` naming `specialist` if
    the content isn't valid JSON or the top-level value isn't an object.
    """
    text = strip_outer_fence(content, specialist)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{specialist} response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{specialist} response must be a JSON object, got {type(data).__name__}")
    return data


def normalize_model_family(value: Any, allowed: dict[str, tuple[str, ...]], specialist: str) -> str:
    """Map the LLM's free-text `model_family` onto one canonical key of `allowed`.

    Whole-phrase, word-boundary matching against a separator-normalized (`-`/`_`
    collapsed to spaces) lowercase copy of `value` — the same robustness approach
    as `feature_engineer._is_target_encoding_method`, so `xgb`, `XGBoost`,
    `light-gbm`, `ExtraTrees`, ... all resolve. Always returns the canonical key,
    never the LLM's raw string: `coder` (T-029) dispatches on this value.

    Raises `ValueError` when `value` is not a non-empty string, when no family
    matches, or when two or more do (e.g. `"xgboost or lightgbm"`) — an ambiguous
    response is rejected rather than resolved by precedence.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{specialist} response missing required non-empty string field "
            f"'model_family', got {value!r}"
        )
    normalized = re.sub(r"[-_]+", " ", value.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    matched = sorted(
        family
        for family, aliases in allowed.items()
        if any(re.search(rf"\b{re.escape(alias)}\b", normalized) for alias in aliases)
    )
    if not matched:
        raise ValueError(
            f"{specialist} response 'model_family' {value!r} is not a supported model "
            f"family; expected one of {sorted(allowed)}"
        )
    if len(matched) > 1:
        raise ValueError(
            f"{specialist} response 'model_family' {value!r} is ambiguous — it names "
            f"{matched}; choose exactly one model family"
        )
    return matched[0]


def _is_json_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _reject_forbidden_cv_keys_in(mapping: Any, location: str, specialist: str) -> None:
    if not isinstance(mapping, dict):
        return
    for key in mapping:
        if key in FORBIDDEN_CV_KEYS:
            raise ValueError(
                f"{specialist} response must not redefine cross-validation: forbidden key "
                f"{key!r} found {location}. The folds are frozen in {CV_STRATEGY_REF} "
                "(write-once) and the design may only reference them."
            )


def _reject_forbidden_cv_keys(data: dict[str, Any], specialist: str) -> None:
    """Reject any attempt to redefine cross-validation, by exact key name, at the
    top level and inside `search_space`/`fixed_params`.

    Checked before anything else so a CV-redefining response fails on that
    specific ground rather than on some incidental schema error. Rejected loudly
    instead of silently dropped by the whitelist rebuild below, so "the design
    does not redefine CV" is an assertable behavior rather than vacuously true.
    """
    _reject_forbidden_cv_keys_in(data, "at the top level", specialist)
    _reject_forbidden_cv_keys_in(data.get("search_space"), "inside 'search_space'", specialist)
    _reject_forbidden_cv_keys_in(data.get("fixed_params"), "inside 'fixed_params'", specialist)


def _validate_bound(
    param: str, key: str, value: Any, param_type: str, specialist: str
) -> int | float:
    # `isinstance(True, int)` is True in Python, so booleans must be rejected
    # explicitly *before* the numeric check — same trap guarded in
    # `validation_strategist._validate_fold_payload`/`_research_common`.
    if isinstance(value, bool):
        raise ValueError(
            f"{specialist} response 'search_space.{param}.{key}' must be a number, "
            f"got boolean {value!r}"
        )
    if param_type == "int":
        if not isinstance(value, int):
            raise ValueError(
                f"{specialist} response 'search_space.{param}.{key}' must be an int for an "
                f"'int' parameter, got {value!r}"
            )
    elif not isinstance(value, (int, float)):
        raise ValueError(
            f"{specialist} response 'search_space.{param}.{key}' must be a number, got {value!r}"
        )
    if not math.isfinite(value):
        # `json.loads` happily parses the bare `Infinity`/`NaN` tokens, so a
        # non-finite bound reaches this validator as a real float.
        raise ValueError(
            f"{specialist} response 'search_space.{param}.{key}' must be finite, got {value!r}"
        )
    return value


def _validate_step(param: str, value: Any, param_type: str, specialist: str) -> int | float:
    if isinstance(value, bool):
        raise ValueError(
            f"{specialist} response 'search_space.{param}.step' must be a number, "
            f"got boolean {value!r}"
        )
    if param_type == "int":
        if not isinstance(value, int):
            raise ValueError(
                f"{specialist} response 'search_space.{param}.step' must be an int for an "
                f"'int' parameter, got {value!r}"
            )
    elif not isinstance(value, (int, float)):
        raise ValueError(
            f"{specialist} response 'search_space.{param}.step' must be a number, got {value!r}"
        )
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"{specialist} response 'search_space.{param}.step' must be a finite number "
            f"greater than 0, got {value!r}"
        )
    return value


def _validate_log(param: str, spec: dict[str, Any], low: int | float, specialist: str) -> bool:
    log = spec["log"]
    if not isinstance(log, bool):
        raise ValueError(
            f"{specialist} response 'search_space.{param}.log' must be a boolean, got {log!r}"
        )
    if log and low <= 0:
        raise ValueError(
            f"{specialist} response 'search_space.{param}' sets 'log': true, which requires "
            f"'low' > 0, got low={low!r}"
        )
    if log and "step" in spec:
        # Optuna's suggest_int/suggest_float raise on this combination at trial
        # time — fail here, before the experiment is ever written to disk.
        raise ValueError(
            f"{specialist} response 'search_space.{param}' combines 'log': true with 'step'; "
            "Optuna rejects that combination — use one or the other"
        )
    return log


def _validate_numeric_param(
    param: str, spec: dict[str, Any], param_type: str, specialist: str
) -> dict[str, Any]:
    for key in ("low", "high"):
        if key not in spec:
            raise ValueError(
                f"{specialist} response 'search_space.{param}' of type {param_type!r} requires "
                f"a {key!r} bound"
            )
    low = _validate_bound(param, "low", spec["low"], param_type, specialist)
    high = _validate_bound(param, "high", spec["high"], param_type, specialist)
    if low >= high:
        raise ValueError(
            f"{specialist} response 'search_space.{param}' requires 'low' < 'high', "
            f"got low={low!r}, high={high!r}"
        )

    validated: dict[str, Any] = {"type": param_type, "low": low, "high": high}
    if "log" in spec:
        validated["log"] = _validate_log(param, spec, low, specialist)
    if "step" in spec:
        validated["step"] = _validate_step(param, spec["step"], param_type, specialist)
    return validated


def _validate_choices(param: str, value: Any, specialist: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(
            f"{specialist} response 'search_space.{param}.choices' must be a non-empty list, "
            f"got {value!r}"
        )
    seen: set[tuple[str, Any]] = set()
    for choice in value:
        if not _is_json_scalar(choice):
            raise ValueError(
                f"{specialist} response 'search_space.{param}.choices' must contain only JSON "
                f"scalars (string/number/boolean/null), got {choice!r}"
            )
        # Keyed by type name as well as value so `1` and `True` (equal and
        # equally-hashed in Python) are not conflated into a false duplicate.
        key = (type(choice).__name__, choice)
        if key in seen:
            raise ValueError(
                f"{specialist} response 'search_space.{param}.choices' must not repeat a "
                f"choice, got {choice!r} more than once"
            )
        seen.add(key)
    return list(value)


def _validate_param_spec(param: str, spec: Any, specialist: str) -> dict[str, Any]:
    """Validate and rebuild one `search_space` entry from its own key whitelist
    (`type`/`low`/`high`/`step`/`log`, or `type`/`choices`); unknown inner keys
    are dropped silently."""
    if not isinstance(spec, dict):
        raise ValueError(
            f"{specialist} response 'search_space.{param}' must be an object with a 'type' "
            f"field, got {spec!r}"
        )
    param_type = spec.get("type")
    if not isinstance(param_type, str) or param_type not in PARAM_TYPES:
        raise ValueError(
            f"{specialist} response 'search_space.{param}' has unsupported 'type' "
            f"{param_type!r}; expected one of {list(PARAM_TYPES)}"
        )
    if param_type == "categorical":
        return {
            "type": param_type,
            "choices": _validate_choices(param, spec.get("choices"), specialist),
        }
    return _validate_numeric_param(param, spec, param_type, specialist)


def _validate_search_space(value: Any, specialist: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(
            f"{specialist} response missing required object field 'search_space', got {value!r}"
        )
    if not value:
        raise ValueError(
            f"{specialist} response 'search_space' must not be empty — an experiment with no "
            "tunable parameters gives Optuna nothing to search"
        )
    return {param: _validate_param_spec(param, spec, specialist) for param, spec in value.items()}


def _validate_fixed_params(value: Any, specialist: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(
            f"{specialist} response missing required object field 'fixed_params' (use an empty "
            f"object when there are none), got {value!r}"
        )
    for key, item in value.items():
        if _is_json_scalar(item):
            continue
        if isinstance(item, list) and all(_is_json_scalar(entry) for entry in item):
            continue
        raise ValueError(
            f"{specialist} response 'fixed_params.{key}' must be a JSON scalar or a flat list "
            f"of scalars — a nested object cannot be passed to the model constructor, "
            f"got {item!r}"
        )
    return dict(value)


def _validate_preprocessing(value: Any, specialist: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(
            f"{specialist} response missing required list field 'preprocessing' (use an empty "
            f"list when there are none), got {value!r}"
        )
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"{specialist} response 'preprocessing[{i}]' must be a non-empty string, "
                f"got {item!r}"
            )
    return list(value)


def _validate_rationale(value: Any, specialist: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{specialist} response missing required non-empty string field 'rationale'"
        )
    return value


def _reject_param_name_collisions(
    search_space: dict[str, Any], fixed_params: dict[str, Any], specialist: str
) -> None:
    collisions = sorted(set(search_space) & set(fixed_params))
    if collisions:
        raise ValueError(
            f"{specialist} response declares {collisions} in both 'search_space' and "
            "'fixed_params' — each parameter must be either tuned or fixed, never both"
        )


def validate_experiment_design(
    data: dict[str, Any],
    *,
    specialist: str,
    allowed_families: dict[str, tuple[str, ...]],
    feature_spec_ref: str,
) -> dict[str, Any]:
    """Validate an LLM-authored experiment design and rebuild it from a whitelist.

    Returns a **fresh** dict whose keys are exactly `DESIGN_KEYS`, in that order —
    the LLM's own object is never written through (same convention as
    `solution_architect`/`feature_engineer`/`baseline_designer`). `specialist`,
    `feature_spec_ref` and `cv_strategy_ref` are injected by the pipeline, never
    read from the response; every other unknown top-level key the LLM sent
    (including `n_trials`/`early_stopping_patience`, which belong to
    `config/settings.yaml`'s `optuna:` block) is dropped by the rebuild.

    Raises `ValueError` naming `specialist` on any violation.
    """
    _reject_forbidden_cv_keys(data, specialist)
    model_family = normalize_model_family(data.get("model_family"), allowed_families, specialist)
    search_space = _validate_search_space(data.get("search_space"), specialist)
    fixed_params = _validate_fixed_params(data.get("fixed_params"), specialist)
    preprocessing = _validate_preprocessing(data.get("preprocessing"), specialist)
    rationale = _validate_rationale(data.get("rationale"), specialist)
    _reject_param_name_collisions(search_space, fixed_params, specialist)

    return {
        "specialist": specialist,
        "model_family": model_family,
        "search_space": search_space,
        "fixed_params": fixed_params,
        "preprocessing": preprocessing,
        "rationale": rationale,
        "feature_spec_ref": feature_spec_ref,
        "cv_strategy_ref": CV_STRATEGY_REF,
    }


def read_fold_summary(state: LabState, workspace: WorkspaceManager) -> str:
    """Render the frozen fold config as a prompt-safe summary: `strategy`,
    `n_folds` and `seed` only — never `fold_indices`, which is a per-row index
    listing that would flood the context window with data the specialist has no
    use for (it designs against the folds, it does not re-derive them).

    Degrades to a placeholder string, never raises, when
    `state["validation_config_path"]` is unset, unreadable, or holds a non-object
    payload — Phase 5 can legitimately be exercised standalone with no Phase 1 run
    ahead of it (same convention as `baseline_designer._read_problem_definition`).
    """
    path = state.get("validation_config_path") or ""
    if not path:
        return _FOLDS_NOT_AVAILABLE
    try:
        data = workspace.read_json(relative_to_workspace(path, workspace))
    except OSError:
        return f"(unable to read frozen fold config at {path})"
    if not isinstance(data, dict):
        return _FOLDS_NOT_AVAILABLE
    return json.dumps({key: data.get(key) for key in _FOLD_SUMMARY_KEYS}, indent=2)


def resolve_feature_spec_ref(state: LabState, workspace: WorkspaceManager) -> str:
    """Workspace-relative pointer to the feature spec this design builds on.

    Uses `state["feature_spec_path"]` (re-relativized — an absolute host path
    baked into `design.json` breaks inside `code_executor`'s subprocess and inside
    the container, where the workspace is bind-mounted elsewhere) and falls back to
    `FEATURE_SPEC_FALLBACK_PATTERN` for the current iteration when `feature_engineer`
    hasn't run yet. Never returns `""`.
    """
    path = state.get("feature_spec_path") or ""
    if path:
        return relative_to_workspace(path, workspace)
    return FEATURE_SPEC_FALLBACK_PATTERN.format(iteration=state["current_iteration"])
