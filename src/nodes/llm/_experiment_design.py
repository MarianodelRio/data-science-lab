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
the same `node_name`-parameter convention `_research_common.py` uses. Every
validation failure raises `ValueError` (never `OverflowError`, `TypeError`, or
a bare `json` exception), so a future critic-retry wrapper has exactly one
exception type to catch.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
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
# MUST stay in sync with `config/agents/feature_engineer.yaml`'s
# `output_file_pattern`. Deliberately duplicated rather than read via
# `load_agent_config("feature_engineer")`: resolving one agent's config at
# another agent's runtime would couple two otherwise-independent nodes and make
# this module fail whenever `feature_engineer`'s YAML is absent or renamed.
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
#
# HONEST SCOPE: this is a **tripwire against explicit CV redefinition**, not a
# proof that the frozen folds are respected. A model can still carve its own
# holdout out of the training fold through model-side hyperparameters that are
# not CV keys at all (`validation_fraction`, `early_stopping`, `n_iter_no_change`,
# `eval_set`, ...). Those are deliberately NOT banned here: early stopping against
# the fold's own validation split is legitimate practice, and forbidding it would
# be a modeling decision made unilaterally on behalf of T-025–T-028. Whoever
# builds `coder` (T-029) / `score_evaluator` (T-031) must look for that escape
# hatch at code-generation time — see the T-024 entry in `context/discoveries.md`.
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

# A hyperparameter name becomes a Python keyword argument in the training code
# `coder` (T-029) generates and `code_executor` runs, so it must be a plain
# identifier: no quotes, newlines, parentheses, backslashes or dunder names of
# unbounded length. Every real hyperparameter of the supported model families
# satisfies this.
_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

# `preprocessing` entries name a step, they do not express one: a token, never a
# call. This is a *shape* constraint only, and deliberately not a closed
# vocabulary — which techniques are meaningful is T-025–T-028's decision, not this
# module's. It rejects the executable-looking form (a call, an expression, a prose
# sentence), e.g. `"StratifiedKFold(n_splits=3, shuffle=True)"`. It does NOT judge
# meaning: `["stratified_kfold"]`, `["cv"]` and `["custom_holdout_split"]` are all
# accepted tokens. Same tripwire framing as `FORBIDDEN_CV_KEYS` — see its note.
_PREPROCESSING_STEP_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Beyond 2**53 an int is no longer exactly representable as an IEEE-754 double,
# and `math.isfinite` raises `OverflowError` on a sufficiently large Python int
# rather than returning `False` — range-check before the finiteness check so the
# module's "always `ValueError`" contract holds.
_MAX_EXACT_INT = 2**53

# `step` is compared against `high - low` with a relative slack, because binary
# floating point makes that subtraction lossy: `0.3 - 0.1` is
# `0.19999999999999998`, so the perfectly legitimate two-value grid
# low=0.1/high=0.3/step=0.2 would be rejected while low=0.1/high=0.7/step=0.6
# passes — an input-dependent false rejection is worse than no check at all on a
# node with no retry wrapper. The slack is far too small to readmit the real bug
# (a step several times the range).
_STEP_RANGE_TOLERANCE = 1 + 1e-9

_FOLD_SUMMARY_KEYS = ("strategy", "n_folds", "seed")
_FOLDS_NOT_AVAILABLE = "(frozen fold config not yet available)"

# Everything an upstream-artifact read may throw at a node that promises to
# degrade rather than abort the graph. Deliberately wide, and each member earns
# its place: `OSError` — file missing, unreadable, a directory. `ValueError` —
# `json.JSONDecodeError` (truncated/empty JSON) and `UnicodeDecodeError` (invalid
# UTF-8), both `ValueError` subclasses, plus `WorkspaceManager._resolve`/
# `Path.relative_to` rejecting a path that is absolute outside the workspace root
# or contains a `..` component. `RecursionError` — a pathologically nested
# payload (~993 levels) exhausts the interpreter's stack inside `json.loads`, and
# again inside `json.dumps` on the way back out; it is a `RuntimeError`, so
# neither of the other two catches it.
#
# Reader helpers in the sibling node modules (`feature_engineer`,
# `baseline_designer`, `solution_architect`, `_research_common`) still catch
# `OSError` alone despite carrying the same "never raises" promise — see the
# T-024 entry in `context/discoveries.md`; fixing them is out of this task's
# scope, but this module's own readers are consistent with each other.
DEGRADE_ERRORS = (OSError, ValueError, RecursionError)


def _strip_outer_fence(content: str, specialist: str) -> str:
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


def _parse_json(text: str, specialist: str) -> Any:
    # A bare `except ValueError` rather than `except json.JSONDecodeError`:
    # `json.loads` also raises a plain `ValueError` for an integer literal beyond
    # CPython's 4300-digit conversion limit, which `JSONDecodeError` would miss
    # and let escape unwrapped (and unattributed to a specialist).
    try:
        return json.loads(text)
    except ValueError as e:
        raise ValueError(f"{specialist} response is not valid JSON: {e}") from e


def _slice_outermost_braces(text: str) -> str | None:
    """The substring from the first `{` to the last `}`, or `None` if there isn't
    one — the salvage window for a response that wrapped its JSON in prose."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + 1]


def extract_json_object(content: str, specialist: str) -> dict[str, Any]:
    """Extract a top-level JSON object from an LLM response.

    Accepts raw JSON with no fence, or the entire response wrapped in a single
    ```json or unlabeled ``` fence. If **either** the fence handling or the parse
    fails, retries once on the substring of the raw response between the first
    `{` and the last `}` — which salvages the three common wrapper failures: a
    sentence of preamble, a sentence of postamble after a closed fence, and a
    fence the response never closed. Raises `ValueError` naming `specialist` —
    with the *original* error, not the salvage attempt's — when the salvage is
    unavailable or also fails, or when the top-level value isn't an object.

    Fail-closed by construction: the salvage only ever hands `json.loads` one
    contiguous substring of the response. It widens what reaches the validator;
    it never weakens what the validator accepts.

    This is deliberately more permissive than the sibling structured-output nodes
    (`baseline_designer`, `feature_engineer`, `solution_architect`), which reject
    outright: this validator has ~40 reject paths and sits on a node with no
    retry wrapper (Phase 5's `code_critic` targets `coder`, not the specialists),
    so a stray sentence must not abort the whole run.
    """
    try:
        data = _parse_json(_strip_outer_fence(content, specialist), specialist)
    except ValueError as first_error:
        salvaged = _slice_outermost_braces(content)
        if salvaged is None or salvaged == content.strip():
            raise
        try:
            data = _parse_json(salvaged, specialist)
        except ValueError:
            raise first_error from None
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
    """A value that survives a `json.dump`/`JSON.parse` round trip unchanged.

    Two exclusions, both of which `WorkspaceManager.write_json` would otherwise
    write happily (it uses `json.dump`'s default `allow_nan=True`) while any
    non-Python consumer silently misreads the result:

    - **Non-finite floats** — emitted as the bare `Infinity`/`NaN` tokens, which
      are valid Python but not RFC-8259; `JSON.parse` rejects them outright, and
      a tolerant parser turns them into `null`.
    - **Ints beyond ±2**53** — Python's arbitrary-precision ints serialize in
      full, but every IEEE-754 double consumer rounds them: `2**53 + 1` reads
      back as `2**53`, and `10**400` reads back as `Infinity`/`null`. The same
      limit `_validate_numeric` applies to bounds, applied here to `choices` and
      `fixed_params` values.

    `bool` is matched before `int` (it is a subclass) so `true`/`false` stay
    scalars regardless of the magnitude check.
    """
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return abs(value) <= _MAX_EXACT_INT
    if isinstance(value, float):
        return math.isfinite(value)
    return False


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
    See `FORBIDDEN_CV_KEYS` for what this guard does and does not prove.
    """
    _reject_forbidden_cv_keys_in(data, "at the top level", specialist)
    _reject_forbidden_cv_keys_in(data.get("search_space"), "inside 'search_space'", specialist)
    _reject_forbidden_cv_keys_in(data.get("fixed_params"), "inside 'fixed_params'", specialist)


def _validate_param_name(name: Any, field: str, specialist: str) -> str:
    if not isinstance(name, str) or not _PARAM_NAME_RE.fullmatch(name):
        raise ValueError(
            f"{specialist} response {field} key {name!r} is not a usable parameter name — "
            f"it must match {_PARAM_NAME_RE.pattern}, because it becomes a Python keyword "
            "argument in the generated training code"
        )
    return name


def _validate_numeric(
    param: str,
    field: str,
    value: Any,
    param_type: str,
    specialist: str,
    *,
    positive: bool = False,
) -> int | float:
    """Validate one numeric field (`low`, `high` or `step`) of a `search_space`
    entry. `positive=True` additionally requires `value > 0` (used for `step`)."""
    label = f"{specialist} response 'search_space.{param}.{field}'"
    # `isinstance(True, int)` is True in Python, so booleans must be rejected
    # explicitly *before* the numeric check — same trap guarded in
    # `validation_strategist._validate_fold_payload`/`_research_common`.
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number, got boolean {value!r}")
    if param_type == "int":
        if not isinstance(value, int):
            raise ValueError(f"{label} must be an int for an 'int' parameter, got {value!r}")
    elif not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number, got {value!r}")
    if isinstance(value, int) and abs(value) > _MAX_EXACT_INT:
        raise ValueError(
            f"{label} is too large to be represented exactly (limit ±2**53), got {value!r}"
        )
    if not math.isfinite(value):
        # `json.loads` happily parses the bare `Infinity`/`NaN` tokens, so a
        # non-finite bound reaches this validator as a real float.
        raise ValueError(f"{label} must be finite, got {value!r}")
    if positive and value <= 0:
        raise ValueError(f"{label} must be greater than 0, got {value!r}")
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
    low = _validate_numeric(param, "low", spec["low"], param_type, specialist)
    high = _validate_numeric(param, "high", spec["high"], param_type, specialist)
    if low >= high:
        raise ValueError(
            f"{specialist} response 'search_space.{param}' requires 'low' < 'high', "
            f"got low={low!r}, high={high!r}"
        )

    validated: dict[str, Any] = {"type": param_type, "low": low, "high": high}
    if "log" in spec:
        validated["log"] = _validate_log(param, spec, low, specialist)
    if "step" in spec:
        step = _validate_numeric(param, "step", spec["step"], param_type, specialist, positive=True)
        if step > (high - low) * _STEP_RANGE_TOLERANCE:
            # Optuna does not reject this: `suggest_int(low=1, high=2, step=5)`
            # silently returns the same value on every trial, so the whole trial
            # budget is spent never tuning the parameter. Same class of silent
            # waste `_validate_log` rejects `log` + `step` for.
            raise ValueError(
                f"{specialist} response 'search_space.{param}' has 'step' {step!r} larger than "
                f"its range ({high!r} - {low!r}); the parameter would collapse to a constant"
            )
        validated["step"] = step
    return validated


def _validate_choices(param: str, value: Any, specialist: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(
            f"{specialist} response 'search_space.{param}.choices' must be a non-empty list, "
            f"got {value!r}"
        )
    seen: set[Any] = set()
    for choice in value:
        if not _is_json_scalar(choice):
            raise ValueError(
                f"{specialist} response 'search_space.{param}.choices' must contain only finite "
                f"JSON scalars (string/number/boolean/null), got {choice!r}"
            )
        # Deduped by *value*, so `1`, `1.0` and `True` count as the same choice:
        # Optuna's `CategoricalDistribution` maps all three to the same internal
        # index, so a trial that trained on `True` is recorded as `1` and the
        # winning configuration is no longer reproducible by `coder`.
        if choice in seen:
            raise ValueError(
                f"{specialist} response 'search_space.{param}.choices' must not repeat a "
                f"choice, got {choice!r} more than once (values that compare equal — e.g. "
                "1, 1.0 and true — are the same choice to Optuna)"
            )
        seen.add(choice)
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
    return {
        _validate_param_name(param, "'search_space'", specialist): _validate_param_spec(
            param, spec, specialist
        )
        for param, spec in value.items()
    }


def _validate_fixed_params(value: Any, specialist: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(
            f"{specialist} response missing required object field 'fixed_params' (use an empty "
            f"object when there are none), got {value!r}"
        )
    validated: dict[str, Any] = {}
    for key, item in value.items():
        name = _validate_param_name(key, "'fixed_params'", specialist)
        if not _is_json_scalar(item) and not (
            isinstance(item, list) and all(_is_json_scalar(entry) for entry in item)
        ):
            raise ValueError(
                f"{specialist} response 'fixed_params.{key}' must be a finite JSON scalar or a "
                f"flat list of them — a nested object cannot be passed to the model "
                f"constructor, and a non-finite number is not valid JSON, got {item!r}"
            )
        validated[name] = item
    return validated


def _validate_preprocessing(value: Any, specialist: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(
            f"{specialist} response missing required list field 'preprocessing' (use an empty "
            f"list when there are none), got {value!r}"
        )
    for i, item in enumerate(value):
        if not isinstance(item, str) or not _PREPROCESSING_STEP_RE.fullmatch(item):
            raise ValueError(
                f"{specialist} response 'preprocessing[{i}]' must name a step as a lower_snake "
                f"token matching {_PREPROCESSING_STEP_RE.pattern} (e.g. "
                f"'native_categorical_handling') — not a call, an expression, or free prose, "
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

    Never raises — see `DEGRADE_ERRORS` for the caught set and why it is that
    wide. An unset, non-string, unreachable, corrupt, pathologically nested or
    non-object fold config all degrade to a placeholder string, because Phase 5
    can legitimately run with no Phase 1 run ahead of it and a malformed upstream
    artifact must not abort the whole graph (same convention as
    `baseline_designer._read_problem_definition`). Both the read *and* the
    re-serialization sit inside the guard: a deeply nested payload blows the
    recursion limit on the way out as readily as on the way in.
    """
    path = state.get("validation_config_path") or ""
    if not isinstance(path, str) or not path:
        return _FOLDS_NOT_AVAILABLE
    try:
        data = workspace.read_json(relative_to_workspace(path, workspace))
        if not isinstance(data, dict):
            return _FOLDS_NOT_AVAILABLE
        return json.dumps({key: data.get(key) for key in _FOLD_SUMMARY_KEYS}, indent=2)
    except DEGRADE_ERRORS:
        return f"(unable to read frozen fold config at {path})"


def resolve_feature_spec_ref(state: LabState, workspace: WorkspaceManager) -> str:
    """Workspace-relative pointer to the feature spec this design builds on.

    Uses `state["feature_spec_path"]` (re-relativized — an absolute host path
    baked into `design.json` breaks inside `code_executor`'s subprocess and inside
    the container, where the workspace is bind-mounted elsewhere) and falls back to
    `FEATURE_SPEC_FALLBACK_PATTERN` for the current iteration whenever that path is
    unset, unusable, or would escape the workspace.

    Never raises and never returns `""`. The fallback also covers a resumed run
    whose recorded absolute path no longer sits under the (moved, renamed or
    differently bind-mounted) workspace root — `relative_to_workspace` raises
    `ValueError` there — and a stored `..` traversal, which must never reach
    `design.json` since `coder` resolves this pointer relative to the workspace.
    """
    fallback = FEATURE_SPEC_FALLBACK_PATTERN.format(iteration=state["current_iteration"])
    path = state.get("feature_spec_path") or ""
    if not isinstance(path, str) or not path:
        return fallback
    try:
        ref = relative_to_workspace(path, workspace)
    except DEGRADE_ERRORS:
        return fallback
    resolved = Path(ref)
    if resolved.is_absolute() or ".." in resolved.parts:
        return fallback
    return ref
