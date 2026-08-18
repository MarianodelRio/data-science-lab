"""score_evaluator: the first node of Pipeline Phase 6 (Evaluation). Reads the
just-completed experiment's `results.json`, normalizes its score to
"higher is better", compares it against the running best, and updates
`LabState`'s scoring fields — the sole writer of `best_score`,
`best_experiment_path`, `last_score`, `score_delta` and
`iterations_without_improvement` anywhere in `src/`.

Runs first in `config/phases/phase6_evaluation.yaml`'s sequence
(`score_evaluator -> feature_importance_extractor -> error_analyst ->
hypothesis_generator -> experiment_designer`), pure Python, no LLM.

**Experiment-directory resolution and JSON reading** are delegated to
`src.nodes.compute._evaluation_common` — see that module's docstring for why
it is a private shared module rather than a duplicated reader.

## Score-direction normalization (CLAUDE.md invariant #3)

`LabState` carries no polarity field (docs/pipeline.md § State), so this node
is where a metric that is better when *lower* (RMSE, LogLoss, MAE, ...) gets
sign-flipped before ever touching `best_score`/`last_score`. `success_metric`
(from `problem_definition.json`) is matched, case-insensitively and with every
non-alphanumeric separator stripped, against a curated minimize-set
(`_MINIMIZE_METRICS`); anything else — including an absent or unrecognized
metric — defaults to "maximize". The separator-normalized matching (rather
than an exact-string match) is deliberate: real `problem_definition.json`
output spells the same metric multiple ways (`"log_loss"`, `"Log-Loss"`,
`"Log Loss"`), and an exact match would silently default those to "maximize"
and get the sign backwards. This resolves the 2026-08-04
`infra-agent (T-002) -> pipeline-agent (T-031)` polarity discovery.

## Liveness — `iterations_without_improvement` increments even with no score

`src/graph/supervisor.py:31-33` makes `iterations_without_improvement >=
max_iterations` the *only* exit from the Phase 6 -> Phase 4 iteration loop,
and this node is the field's sole writer. So an iteration that produced no
scorable result (missing/malformed `results.json`, no valid `cv_score`) still
increments the counter — not incrementing would let a permanently-broken
`results.json` loop the pipeline forever. The "nothing to evaluate" vs.
"evaluated and didn't improve" distinction that gets lost from the counter is
preserved in the artifact's `evaluated`/`reason` fields instead. See
`context/decisions.md` (2026-08-17) for the full reasoning — this overrides
what would otherwise be the more symmetric-looking choice.

## Baseline comparison is informational only

`delta_vs_baseline` (raw, not normalized — the baseline is on its own scale)
is computed only when `results.json`'s optional `metric` field normalizes into
one of `{accuracy, r2, rsquared, score}` — the token set `baseline_runner`'s
own `.score()` convention (T-020) can produce — AND `state["baseline_score"]`
is a finite number. It never influences `best_score`, `best_experiment_path`,
or `is_improvement`; see `context/decisions.md` for why this comparability
rule is a new contract this task invents (no landed `coder` output defines
`results.json`'s `metric` field yet).

## Output iteration and the experiment-resolution warning

The artifact's filename number and its `iteration` field come from
`_evaluation_common.resolve_output_iteration`, not directly from
`resolve_iteration` — the report is named after the `experiment_dir` actually
read, not after whatever an `experiments` entry's `iteration` key claims (they
can diverge; see that function's docstring). When they diverge because the
entry's `path` was unusable and the well-known fallback directory was read
instead, `experiment_resolution_warning` names both — this is adversarial
review's finding: without it, a stale fallback read could silently produce a
false-positive `is_improvement` filed under a number that doesn't match what
was actually read, with nothing downstream able to tell.

## Never non-finite

Writes `reports/score_evaluation_{iteration}.json` unconditionally (both the
"no valid score" path and the happy path) via `WorkspaceManager.write_json`.
Per that method's `json.dump` default behavior, a non-finite float written
into the artifact would serialize as invalid JSON tokens (`Infinity`, `NaN`)
that flow through the SQLite checkpointer — so every float in the artifact is
either already known-finite or explicitly downgraded to `None`/JSON `null`
before being written (see `_coerce_finite_float` and the `best_score_before`/
`best_score_after` handling in `run`). This is not just about individual
inputs: `score_delta` and `delta_vs_baseline` are each a *subtraction* of two
individually-finite operands, and a subtraction's result is not automatically
finite (two large finite floats of opposite sign can overflow to `+-inf`) —
`run` re-checks each result with `math.isfinite` and degrades it explicitly
(`score_delta` to `0.0`, consistent with the first-evaluation rule;
`delta_vs_baseline` to `None`, consistent with every other "not comparable"
path for that field) rather than trusting operand-level finiteness alone.
"""

from __future__ import annotations

import math
import re
from typing import Any

from src.nodes.compute import _evaluation_common
from src.nodes.compute.base import ComputeNode
from src.state import LabState

# Matches `baseline_runner`'s own results-file key (`baseline_runner.py:126,201`)
# -- the only landed precedent for this file's shape. Pinned as a contract for
# `coder` (T-029) in `context/discoveries.md`.
_RESULTS_SCORE_KEY = "cv_score"

_OUTPUT_PATTERN = "reports/score_evaluation_{iteration}.json"

# Curated minimize-oriented metrics, already stored in the same
# separator-normalized form `_normalize_metric_name` produces (lowercase, no
# non-alphanumeric characters) so membership checks never need to re-normalize
# this table itself.
_MINIMIZE_METRICS = frozenset(
    {
        "rmse",
        "mse",
        "mae",
        "mape",
        "smape",
        "rmsle",
        "msle",
        "medae",
        "logloss",
        "crossentropy",
        "brier",
        "hammingloss",
        "wmae",
    }
)

# `results.json`'s optional `metric` field must normalize into one of these
# tokens for a `delta_vs_baseline` comparison to be made -- `baseline_score` is
# each estimator's own accuracy/R^2 `.score()` value (T-020), so comparing it
# against, say, an RMSE would be comparing two different scales.
_BASELINE_COMPATIBLE_METRIC_TOKENS = frozenset({"accuracy", "r2", "rsquared", "score"})

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]")


def _normalize_metric_name(value: Any) -> str:
    """Lowercase and strip every non-alphanumeric character. Collapses
    `"log_loss"`, `"logloss"`, `"Log-Loss"` and `"Log Loss"` to the same
    token. Non-`str`/empty input normalizes to `""` (never matches any curated
    set, so it defaults to "maximize")."""
    if not isinstance(value, str) or not value.strip():
        return ""
    return _NON_ALPHANUMERIC.sub("", value.strip().lower())


def _direction_for_metric(normalized_metric: str) -> str:
    """`"minimize"` for a curated metric, else `"maximize"` -- the default for
    an unknown or absent metric."""
    return "minimize" if normalized_metric in _MINIMIZE_METRICS else "maximize"


def _coerce_finite_float(value: Any) -> float | None:
    """`float(value)` for a finite, non-bool `int`/`float`; `None` for
    anything else (wrong type, `bool`, `NaN`, `+-inf`)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    coerced = float(value)
    return coerced if math.isfinite(coerced) else None


def _score_unavailable_reason(results: dict[str, Any]) -> str:
    if not results:
        return "no readable results.json found in any candidate experiment directory"
    return "results.json has no valid finite numeric 'cv_score' field"


def _is_baseline_comparable_metric(value: Any) -> bool:
    return _normalize_metric_name(value) in _BASELINE_COMPATIBLE_METRIC_TOKENS


def _baseline_comparison_reason(
    raw_score: float | None,
    metric_field: Any,
    baseline_score: float | None,
    *,
    comparable: bool,
    overflowed: bool,
) -> str:
    """Human-readable reason recorded alongside `delta_vs_baseline`, whether or
    not a comparison was actually made."""
    if overflowed:
        return (
            "raw_score and baseline_score are individually finite, but their difference "
            "overflowed to a non-finite value; delta_vs_baseline omitted"
        )
    if comparable:
        return (
            f"results.json declares metric {metric_field!r}, matching baseline_score's own "
            "accuracy/R^2 .score() convention"
        )
    if raw_score is None:
        return "no evaluated score available for this iteration to compare against the baseline"
    if not _is_baseline_comparable_metric(metric_field):
        return (
            f"results.json 'metric' field {metric_field!r} does not normalize into the "
            f"baseline-comparable set {sorted(_BASELINE_COMPATIBLE_METRIC_TOKENS)}"
        )
    return "no finite baseline_score recorded yet"


class ScoreEvaluatorNode(ComputeNode):
    name = "score_evaluator"

    def run(self, state: LabState) -> dict[str, Any]:
        workspace = self.workspace(state)
        experiment_dir, results = _evaluation_common.read_experiment_results(dict(state), workspace)
        problem_definition = _evaluation_common.read_json_dict(
            str(state.get("problem_definition_path") or ""), workspace
        )
        iteration, experiment_resolution_warning = _evaluation_common.resolve_output_iteration(
            dict(state), workspace, experiment_dir
        )

        success_metric_raw = problem_definition.get("success_metric")
        success_metric = _normalize_metric_name(success_metric_raw)
        direction = _direction_for_metric(success_metric)

        raw_score = _coerce_finite_float(results.get(_RESULTS_SCORE_KEY))

        best_score_before, best_score_before_finite = self._read_best_score(state)
        iwi_before = self._read_iterations_without_improvement(state)

        normalized_score: float | None
        if raw_score is None:
            # ORCHESTRATOR OVERRIDE of the plan's original "do not increment
            # when nothing was evaluated" — see the module docstring's
            # "Liveness" section. Not reverted.
            normalized_score = None
            is_improvement = False
            delta: dict[str, Any] = {"iterations_without_improvement": iwi_before + 1}
            best_score_after = best_score_before
        else:
            normalized_score = raw_score if direction == "maximize" else -raw_score
            is_improvement = normalized_score > best_score_before
            score_delta = (
                (normalized_score - best_score_before) if best_score_before_finite else 0.0
            )
            if not math.isfinite(score_delta):
                # Both operands are individually finite (guaranteed by
                # `_coerce_finite_float`/the raw `-inf` read above), but their
                # *subtraction* is not re-checked by that alone: two large
                # finite floats of opposite sign can overflow to `+-inf`.
                # Degrade to the same `0.0` the first-evaluation branch above
                # already uses, rather than ever writing a non-finite float
                # into `LabState` (it would flow into the SQLite checkpointer)
                # or the artifact (invalid JSON).
                score_delta = 0.0
            delta = {
                "last_score": normalized_score,
                "score_delta": score_delta,
                "iterations_without_improvement": 0 if is_improvement else iwi_before + 1,
            }
            if is_improvement:
                delta["best_score"] = normalized_score
                delta["best_experiment_path"] = experiment_dir
            best_score_after = normalized_score if is_improvement else best_score_before

        baseline_score = _coerce_finite_float(state.get("baseline_score"))
        metric_field = results.get("metric")
        baseline_comparable = (
            raw_score is not None
            and _is_baseline_comparable_metric(metric_field)
            and baseline_score is not None
        )
        delta_vs_baseline: float | None = None
        baseline_delta_overflowed = False
        if baseline_comparable and raw_score is not None and baseline_score is not None:
            candidate_delta = raw_score - baseline_score
            # Same overflow guard as `score_delta` above: both operands are
            # individually finite but their subtraction is not. Degrading to
            # `None` here (rather than `0.0`) is consistent with every other
            # "not comparable" path for this field — a `0.0` would falsely
            # read as "no difference from baseline" rather than "the
            # difference could not be computed".
            if math.isfinite(candidate_delta):
                delta_vs_baseline = candidate_delta
            else:
                baseline_delta_overflowed = True

        artifact = {
            "iteration": iteration,
            "experiment_dir": experiment_dir,
            "experiment_resolution_warning": experiment_resolution_warning,
            "evaluated": raw_score is not None,
            "reason": None if raw_score is not None else _score_unavailable_reason(results),
            "raw_score": raw_score,
            "normalized_score": normalized_score,
            "success_metric": success_metric or None,
            "success_metric_raw": (
                success_metric_raw if isinstance(success_metric_raw, str) else None
            ),
            "direction": direction,
            "best_score_before": best_score_before if best_score_before_finite else None,
            "best_score_after": best_score_after if math.isfinite(best_score_after) else None,
            "is_improvement": is_improvement,
            "iterations_without_improvement_after": delta["iterations_without_improvement"],
            "baseline_score": baseline_score,
            "delta_vs_baseline": delta_vs_baseline,
            # Reflects whether a finite delta was actually produced, not mere
            # eligibility — `baseline_comparable` can be True while the
            # subtraction itself still overflows (see `baseline_delta_overflowed`).
            "baseline_comparison_made": delta_vs_baseline is not None,
            "baseline_comparison_reason": _baseline_comparison_reason(
                raw_score,
                metric_field,
                baseline_score,
                comparable=baseline_comparable,
                overflowed=baseline_delta_overflowed,
            ),
        }
        workspace.write_json(_OUTPUT_PATTERN.format(iteration=iteration), artifact)

        return delta

    @staticmethod
    def _read_best_score(state: LabState) -> tuple[float, bool]:
        """`(best_score_before, is_finite)`. The `-inf` sentinel must NOT be
        round-tripped through `_coerce_finite_float` (which rejects
        non-finite values and would collapse it to `None`, losing the "is it
        `-inf` specifically" distinction the artifact and the finite/`-inf`
        `score_delta` branch both need) -- read it raw instead."""
        raw = state.get("best_score")
        is_real_number = isinstance(raw, (int, float)) and not isinstance(raw, bool)
        best_score = float(raw) if is_real_number else float("-inf")
        return best_score, math.isfinite(best_score)

    @staticmethod
    def _read_iterations_without_improvement(state: LabState) -> int:
        raw = state.get("iterations_without_improvement")
        return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0
