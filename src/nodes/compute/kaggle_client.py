"""kaggle_client: submits the run's predictions to Kaggle, reads back the public
leaderboard score, and records the CV/LB divergence in
`reports/kaggle_submission.json`.

**Last** node of `config/phases/phase7_delivery.yaml`'s sequence
(`reviewer -> report_writer -> kaggle_client`), pure Python, no LLM. Phase 7 is
terminal and is reached only when `iterations_without_improvement >=
max_iterations` (`src/graph/supervisor.py:31-33`), so **nothing here may abort
the graph**: by the time this node runs, `reports/final_report.md` already
exists and a crash would take the whole run's deliverable down with it. Every
failure path writes the artifact and returns normally.

## The ordering rule — check the submission file before touching the API

`run`'s sequence is a contract, not a style choice. The submission file's
existence is verified **before the first Kaggle API call**, because the
integration smoke suite (`tests/integration/phases/test_phase_subgraphs_smoke.py`)
runs this real node inside a real compiled Phase 7 subgraph, `kaggle` is
installed in this environment, and `tests/fixtures/graph_mocks.set_fake_provider_env`
sets fake `KAGGLE_USERNAME`/`KAGGLE_KEY`. Move the file check after `submit()`
and that suite makes a live `competition_submit` call against a real
competition slug. `test_no_submission_file_records_not_submitted_and_never_calls_the_api`
asserts the injected API recorded zero calls, and is the gate on this.

## The artifact — exactly 9 keys, always all present

`competition`, `submission_file`, `submitted`, `lb_score`, `cv_score`,
`cv_direction`, `divergence`, `divergence_flag`, `reason`. `reason` is `None`
only on the fully-successful path; otherwise it is the accumulated reasons
joined with `"; "`. `divergence_flag` is **always a `bool`**: `False` covers
both "did not diverge" and "could not be computed", and `reason` is what
disambiguates the two.

Nothing is written to `LabState`. `src/state.py` is a protected contract with no
leaderboard field, so the LB score, the divergence flag and the submission
outcome live in the workspace artifact plus a one-line `messages` summary. Any
future consumer (API/frontend surfacing "did we submit, what did we score")
reads `reports/kaggle_submission.json` — see `context/discoveries/T-033.md`.
This node is in particular **not** a writer of `best_score`/
`best_experiment_path` (CLAUDE.md invariant #3; `score_evaluator` is their sole
writer).

## CV de-normalization

`score_evaluator` normalizes every score to "higher is better" before storing
it, sign-flipping a minimize-oriented metric — so `state["best_score"]` for an
RMSE run is a *negative* number. The Kaggle leaderboard reports the metric in
its own units, so the two are only comparable after de-normalizing:
`cv_raw = best_score if direction == "maximize" else -best_score`. `direction`
is read from `reports/score_evaluation_{current_iteration - 1}.json`'s own
`direction` field — `LabState` carries no polarity field, and re-deriving it
here would duplicate `score_evaluator`'s curated minimize-metric table.

`divergence = (cv_raw - lb)` for `maximize` and `(lb - cv_raw)` for `minimize`,
so a **positive** divergence always means "CV looked better than the
leaderboard", in both polarities.

**Single score-evaluation candidate, no fallback.** Only
`current_iteration - 1` is tried (`experiment_designer` increments
`current_iteration` last in Phase 6, so Phase 7 always observes `N + 1` while
the artifacts on disk are filed under `N`). This is deliberately asymmetric with
`reviewer`'s multi-candidate list: for the divergence *number*, degrading to
`divergence: null` with a reason beats reporting a silently-wrong number derived
from a different iteration's polarity.

**`_DIVERGENCE_THRESHOLD = 0.05` is an absolute difference in the metric's own
units, and is therefore scale-dependent** — an RMSE in the thousands will never
flag, and a metric with a tiny range will always flag. Accepted for v1 because
the flag is advisory and nothing reads it yet; a metric-aware or relative
threshold should be revisited when the first real consumer lands (recorded in
`context/discoveries/T-033.md`). The comparison is a **strict** `>`, so a
divergence sitting exactly at the threshold is not flagged.

## Deliberate asymmetry with `reviewer` in experiment-dir resolution

This node maps `state["best_experiment_path"]` through
`WorkspaceManager.experiment_dir(basename)` because it must hand the Kaggle API
an **absolute** path, and `experiment_dir` is the sanctioned public API for that
(it resolves without creating anything). `src/nodes/llm/reviewer.py` instead
uses the *relativized* path directly, because it reads through
`WorkspaceManager.read_text`, which wants a workspace-relative path. The two
agree for every well-formed value (`experiments/exp_N`); they diverge only for
a nested pointer such as `foo/bar/exp_3`, which this node relocates to
`experiments/exp_3`.

## No LLM import (CLAUDE.md invariant #8)

`_relative_to_workspace`, `_read_json_dict` and `_coerce_finite_float` are
**ported**, not imported: their originals live under `src/nodes/llm/`, which
transitively pulls in `langchain_core`. `src/tools/kaggle_client` is imported as
a *module* (`kaggle_tool`) rather than by symbol, so nothing class-shaped enters
this namespace and `node_resolver._find_node_class` still sees exactly one
class. `tests/unit/nodes/compute/test_kaggle_client.py` asserts the import set
by AST scan.
"""

from __future__ import annotations

import math
from pathlib import Path, PurePosixPath
from typing import Any

from src.nodes.compute.base import ComputeNode
from src.state import LabState
from src.tools import kaggle_client as kaggle_tool
from src.workspace.workspace_manager import WorkspaceManager

_OUTPUT_PATH = "reports/kaggle_submission.json"
_SUBMISSION_FILENAME = "submission.csv"
_SCORE_EVALUATION_PATTERN = "reports/score_evaluation_{iteration}.json"
_EXPERIMENT_DIR_PATTERN = "exp_{iteration}"
_SUBMISSION_MESSAGE = "data-science-lab automated submission (iteration {iteration})"
_VALID_DIRECTIONS = ("maximize", "minimize")

# Absolute difference in the metric's own units — see the module docstring's
# scale-dependence note. `config/settings.yaml` is a protected contract, so this
# lives here rather than in config.
_DIVERGENCE_THRESHOLD = 0.05

# Local copy of `_evaluation_common.DEGRADE_ERRORS` (invariant #8 forbids
# importing the LLM-side twin, and the compute-side one belongs to Phase 6).
DEGRADE_ERRORS: tuple[type[Exception], ...] = (OSError, ValueError, RecursionError)


def _coerce_iteration(value: Any) -> int:
    """`int`, but never `bool` (`isinstance(True, int)` is `True`, and a boolean
    would interpolate into an artifact path as `..._True.json`). Defaults to
    `0` — the same coercion `_evaluation_llm_common.current_iteration` makes."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _coerce_finite_float(value: Any) -> float | None:
    """`float(value)` for a finite, non-`bool` `int`/`float`; `None` otherwise.

    Port of `score_evaluator._coerce_finite_float`. A non-finite float written
    into the artifact would serialize through `json.dump` as the non-RFC-8259
    tokens `Infinity`/`NaN`.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    coerced = float(value)
    return coerced if math.isfinite(coerced) else None


def _relative_to_workspace(path: str, workspace: WorkspaceManager) -> str:
    """Port of `_evaluation_common.relative_to_workspace` — an already-relative
    path passes through unchanged; an absolute path outside the workspace root
    raises `ValueError` via `Path.relative_to`."""
    p = Path(path)
    if not p.is_absolute():
        return path
    return str(p.relative_to(workspace.workspace_path))


def _read_json_dict(relative_path: Any, workspace: WorkspaceManager) -> dict[str, Any]:
    """Read a workspace JSON artifact as a dict, degrading to `{}`. Never
    raises. Same shape as `_evaluation_common.read_json_dict`."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        return {}
    try:
        data = workspace.read_json(_relative_to_workspace(relative_path.strip(), workspace))
    except DEGRADE_ERRORS:
        return {}
    return data if isinstance(data, dict) else {}


def _experiment_basename(candidate: Any, workspace: WorkspaceManager) -> str | None:
    """The bare `exp_N`-style directory name inside `experiments/`, or `None`.

    `WorkspaceManager.experiment_dir` takes an *id*, not a path, and joins it
    under `experiments/`. So a recorded pointer is relativized, rejected if it
    is absolute-outside-root or traverses, and then reduced to its final
    component. A pointer nesting the directory elsewhere (`foo/bar/exp_3`) is
    therefore relocated to `experiments/exp_3` — see the module docstring's
    asymmetry note.
    """
    if not isinstance(candidate, str) or not candidate.strip():
        return None
    try:
        relative = _relative_to_workspace(candidate.strip(), workspace)
    except DEGRADE_ERRORS:
        return None
    pure = PurePosixPath(relative.rstrip("/"))
    if pure.is_absolute() or ".." in pure.parts:
        return None
    name = pure.name
    if name in ("", ".", ".."):
        return None
    return name


def _resolve_submission(
    state: LabState, workspace: WorkspaceManager
) -> tuple[str | None, Path | None]:
    """`(workspace_relative_path, absolute_path)` of the submission CSV.

    Candidates, best guess first: the experiment directory named by
    `state["best_experiment_path"]`, then the previous iteration's well-known
    `experiments/exp_{current_iteration - 1}` directory. The recorded
    `submission_file` is always the **workspace-relative** form, never the
    absolute one — the artifact must stay portable across workspace roots.

    When no candidate holds the file, the absolute path is `None` and the
    relative path names the first candidate that was tried, so the artifact's
    `reason` can point at a concrete place to look.
    """
    previous = _coerce_iteration(state.get("current_iteration")) - 1
    names = [
        _experiment_basename(state.get("best_experiment_path"), workspace),
        _EXPERIMENT_DIR_PATTERN.format(iteration=previous),
    ]

    candidates: list[tuple[str, Path]] = []
    for name in names:
        if name is None:
            continue
        relative = f"experiments/{name}/{_SUBMISSION_FILENAME}"
        if any(relative == existing for existing, _ in candidates):
            continue
        try:
            directory = workspace.experiment_dir(name)
        except DEGRADE_ERRORS:
            continue
        candidates.append((relative, directory / _SUBMISSION_FILENAME))

    for relative, absolute in candidates:
        try:
            found = absolute.is_file()
        except OSError:
            found = False
        if found:
            return relative, absolute

    return (candidates[0][0] if candidates else None), None


def _cv_from_state(
    state: LabState, workspace: WorkspaceManager
) -> tuple[float | None, str | None, str | None]:
    """`(cv_raw, direction, reason)` — the de-normalized CV score and its
    polarity, or `None`s plus the reason they could not be established."""
    iteration = _coerce_iteration(state.get("current_iteration")) - 1
    path = _SCORE_EVALUATION_PATTERN.format(iteration=iteration)
    data = _read_json_dict(path, workspace)
    if not data:
        return None, None, f"no readable score evaluation report at {path}"

    raw_direction = data.get("direction")
    if raw_direction not in _VALID_DIRECTIONS:
        return (
            None,
            None,
            f"score evaluation report {path} declares direction {raw_direction!r}, "
            f"which is not one of {list(_VALID_DIRECTIONS)}",
        )
    direction = str(raw_direction)

    # Read raw, not through `_coerce_finite_float`: the `-inf` sentinel needs to
    # be reported as "no experiment ever improved", not merged into the generic
    # "not a number" case.
    best = state.get("best_score")
    if isinstance(best, bool) or not isinstance(best, (int, float)) or not math.isfinite(best):
        return (
            None,
            direction,
            f"best_score is {best!r}, not a finite number, so there is no CV score to "
            "compare against the leaderboard",
        )

    cv_raw = float(best) if direction == "maximize" else -float(best)
    return cv_raw, direction, None


def _divergence(
    cv_raw: float | None, lb: float | None, direction: str | None
) -> tuple[float | None, bool, str | None]:
    """`(divergence, flag, reason)`.

    Positive divergence always means "CV looked better than the leaderboard",
    in both polarities. The subtraction is re-checked with `math.isfinite`
    (`score_evaluator`'s overflow precedent): two individually-finite operands
    of opposite sign can overflow.
    """
    if direction is None or cv_raw is None:
        return None, False, "CV/leaderboard divergence not computed: no comparable CV score"
    if lb is None:
        return None, False, "CV/leaderboard divergence not computed: no leaderboard score"
    value = (cv_raw - lb) if direction == "maximize" else (lb - cv_raw)
    if not math.isfinite(value):
        return (
            None,
            False,
            "CV/leaderboard divergence not computed: the difference overflowed to a "
            "non-finite value",
        )
    return value, abs(value) > _DIVERGENCE_THRESHOLD, None


def _summary_line(artifact: dict[str, Any]) -> str:
    """One human-readable `messages` line — the only trace of this node outside
    the workspace artifact, since no `LabState` field records it."""
    competition = artifact["competition"]
    if not artifact["submitted"]:
        return (
            f"kaggle_client: no submission made for competition {competition!r}. "
            f"Reason: {artifact['reason']}. Details in {_OUTPUT_PATH}."
        )
    return (
        f"kaggle_client: submitted {artifact['submission_file']} to competition "
        f"{competition!r}; public leaderboard score {artifact['lb_score']}, "
        f"CV score {artifact['cv_score']} ({artifact['cv_direction']}), "
        f"divergence {artifact['divergence']} (flag: {artifact['divergence_flag']}). "
        f"Details in {_OUTPUT_PATH}."
    )


class KaggleClientNode(ComputeNode):
    name = "kaggle_client"

    def __init__(self, *, api: kaggle_tool.KaggleApiProtocol | None = None) -> None:
        """`api` is keyword-only and defaults to `None`, keeping the node
        zero-argument constructible for `resolve_node`'s `cls()` while giving
        unit tests an injection point — the same convention
        `hypothesis_generator`/`solution_architect` use for their `RagStore`.
        `None` makes `src.tools.kaggle_client` build its own API, which checks
        `KAGGLE_USERNAME`/`KAGGLE_KEY` before it even imports `kaggle`."""
        self._api = api

    def run(self, state: LabState) -> dict[str, Any]:
        workspace = self.workspace(state)
        reasons: list[str] = []

        raw_competition = state.get("competition_name")
        if not isinstance(raw_competition, str) or not raw_competition.strip():
            # Guarded here rather than left to `_validate_competition`: a
            # non-`str` would raise `TypeError` out of `re.match`, which is not
            # in the caught set below.
            return self._finish(
                workspace,
                competition=None,
                submission_file=None,
                submitted=False,
                lb=None,
                cv_raw=None,
                direction=None,
                reasons=["competition_name is not a non-empty string"],
            )
        competition = raw_competition.strip()

        cv_raw, direction, cv_reason = _cv_from_state(state, workspace)
        if cv_reason is not None:
            reasons.append(cv_reason)

        submission_file, absolute = _resolve_submission(state, workspace)
        if absolute is None:
            # THE network-safety gate: the API is never touched when there is
            # nothing to submit. See the module docstring's ordering rule.
            reasons.append(
                f"no submission file found at {submission_file or 'any candidate directory'}"
            )
            return self._finish(
                workspace,
                competition=competition,
                submission_file=submission_file,
                submitted=False,
                lb=None,
                cv_raw=cv_raw,
                direction=direction,
                reasons=reasons,
            )

        iteration = _coerce_iteration(state.get("current_iteration"))
        try:
            kaggle_tool.submit(
                competition,
                str(absolute),
                _SUBMISSION_MESSAGE.format(iteration=iteration),
                api=self._api,
            )
        except ValueError as e:
            reasons.append(f"Kaggle rejected the competition slug {competition!r}: {e}")
            return self._finish(
                workspace,
                competition=competition,
                submission_file=submission_file,
                submitted=False,
                lb=None,
                cv_raw=cv_raw,
                direction=direction,
                reasons=reasons,
            )
        except RuntimeError as e:
            reasons.append(f"Kaggle credentials are not available: {e}")
            return self._finish(
                workspace,
                competition=competition,
                submission_file=submission_file,
                submitted=False,
                lb=None,
                cv_raw=cv_raw,
                direction=direction,
                reasons=reasons,
            )
        except Exception as e:  # Phase 7 is terminal - see the module docstring
            # Deliberately broad: the `kaggle` SDK raises its own exception
            # types (HTTP/API errors) that this module must not import, and an
            # abort here would destroy the run after `final_report.md` exists.
            reasons.append(f"submission to competition {competition!r} failed: {e!r}")
            return self._finish(
                workspace,
                competition=competition,
                submission_file=submission_file,
                submitted=False,
                lb=None,
                cv_raw=cv_raw,
                direction=direction,
                reasons=reasons,
            )

        lb = self._read_leaderboard_score(competition, reasons)
        return self._finish(
            workspace,
            competition=competition,
            submission_file=submission_file,
            submitted=True,
            lb=lb,
            cv_raw=cv_raw,
            direction=direction,
            reasons=reasons,
        )

    def _read_leaderboard_score(self, competition: str, reasons: list[str]) -> float | None:
        """The latest submission's `public_score`, or `None` plus a reason.

        `TypeError` is caught **first and named specifically**: the tool picks
        the latest submission with `max(submissions, key=lambda s: s.date)`, and
        a submission whose `date` is `None` (or otherwise unorderable) makes
        that comparison raise — the open T-007 discovery. A generic
        "call failed" reason would leave that indistinguishable from a network
        error. Note it takes **two** submissions for `max` to invoke the key
        comparison at all.
        """
        try:
            result = kaggle_tool.get_score(competition, api=self._api)
        except TypeError as e:
            reasons.append(
                f"could not read the leaderboard score for competition {competition!r}: "
                f"comparing submission 'date' values raised TypeError ({e}) — the Kaggle API "
                "returned a submission whose 'date' is None or otherwise unorderable"
            )
            return None
        except Exception as e:  # Phase 7 is terminal - see the module docstring
            reasons.append(
                f"could not read the leaderboard score for competition {competition!r}: {e!r}"
            )
            return None

        lb = _coerce_finite_float(result.get("public_score"))
        if lb is None:
            reasons.append(
                f"the leaderboard returned public_score {result.get('public_score')!r}, "
                "which is not a finite number"
            )
        return lb

    def _finish(
        self,
        workspace: WorkspaceManager,
        *,
        competition: str | None,
        submission_file: str | None,
        submitted: bool,
        lb: float | None,
        cv_raw: float | None,
        direction: str | None,
        reasons: list[str],
    ) -> dict[str, Any]:
        """Build the 9-key artifact, write it, and return the messages-only
        delta. Every exit from `run` goes through here, so the key set and the
        "artifact is always written" guarantee cannot drift between paths."""
        divergence, divergence_flag, divergence_reason = _divergence(cv_raw, lb, direction)
        if divergence_reason is not None:
            reasons = [*reasons, divergence_reason]

        artifact: dict[str, Any] = {
            "competition": competition,
            "submission_file": submission_file,
            "submitted": submitted,
            "lb_score": lb,
            "cv_score": cv_raw,
            "cv_direction": direction,
            "divergence": divergence,
            "divergence_flag": divergence_flag,
            "reason": "; ".join(reasons) if reasons else None,
        }
        workspace.write_json(_OUTPUT_PATH, artifact)
        return {"messages": [{"role": "assistant", "content": _summary_line(artifact)}]}
