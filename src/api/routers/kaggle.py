"""`/api/runs/{id}/submit` — submit a run's best experiment to Kaggle and
report back the resulting public leaderboard score.

Reads `best_experiment_path` off the run's *live* checkpoint state (via
`src.api.routers.runs._get_or_build_graph_values` — the same cross-router
import `src.api.routers.chat` already uses for `_config`/`_get_or_build_graph`,
rather than a second, parallel helper module), never
`reports/kaggle_submission.json` (a separate record of the automated Phase 7
submission flow) and never falls back to a guessed
`experiments/exp_{current_iteration - 1}` path — an unset or unresolvable
`best_experiment_path`, or a missing `submission.csv`, is always a 409, not a
silent fallback.

`src.tools.kaggle_client` is imported at module scope: that module never
imports the `kaggle` package (which authenticates eagerly) at its own module
scope, so importing this router never risks eager Kaggle authentication.

`request.app.state.active_submissions` (a per-process `set[str]` of `run_id`s
currently mid-submission, initialized in `create_app`) guards against a
double-click/retry/near-simultaneous-request firing two real Kaggle
submissions for the same run: `submit_run` checks-and-409s before its first
`await`, with zero `await` between the check and registering `run_id` into
the set — the same discipline `resume_run` (`src/api/routers/runs.py`)
established for `active_runs`, a distinct concept (pipeline runs, not
submissions) tracked in its own collection.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from src.api import registry
from src.api.models import SubmitResponse
from src.api.registry import RunNotFoundError, RunRecord
from src.api.responses import json_response
from src.api.routers.runs import _get_or_build_graph_values as get_or_build_graph_values
from src.tools import kaggle_client
from src.workspace.workspace_manager import WorkspaceManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["kaggle"])

_SUBMISSION_FILENAME = "submission.csv"
_NO_BEST_EXPERIMENT_DETAIL = "run has no best experiment yet"
_SUBMISSION_IN_PROGRESS_DETAIL = "a submission for this run is already in progress"


def _resolve_submission_path(workspace_path: str, experiment_name: str) -> tuple[Path, bool]:
    """Resolve `experiment_name`'s submission.csv path under the run's
    workspace and check whether it exists.

    Runs entirely via `asyncio.to_thread` in the caller: every step here is
    blocking disk I/O — `WorkspaceManager.__init__` creates the workspace
    root directory tree (`mkdir(parents=True, exist_ok=True)`, matching every
    other `WorkspaceManager` call site in the codebase; no non-creating
    constructor exists, and `WorkspaceManager`'s public API is a protected
    contract this task does not touch), `.experiment_dir()` resolves the
    target path, and `Path.is_file()` stats the file. `OSError`/`ValueError`
    from any of these three (e.g. a malformed/traversal `experiment_name`)
    propagate to the caller for a uniform 409 mapping.
    """
    workspace = WorkspaceManager(workspace_path)
    experiment_directory = workspace.experiment_dir(experiment_name)
    submission_path = experiment_directory / _SUBMISSION_FILENAME
    return submission_path, submission_path.is_file()


@router.post("/{run_id}/submit", response_model=SubmitResponse)
async def submit_run(run_id: str, request: Request) -> Response:
    active_submissions: set[str] = request.app.state.active_submissions
    if run_id in active_submissions:
        raise HTTPException(status_code=409, detail=_SUBMISSION_IN_PROGRESS_DETAIL)
    # No `await` between the check above and this registration: this is what
    # closes the double-submit race (two near-simultaneous requests for the
    # same run_id could otherwise both pass the check before either
    # registration took effect) — same discipline `resume_run`
    # (`src/api/routers/runs.py`) established for `active_runs`.
    active_submissions.add(run_id)
    try:
        return await _do_submit(run_id, request)
    finally:
        active_submissions.discard(run_id)


async def _do_submit(run_id: str, request: Request) -> Response:
    runs_dir: Path = request.app.state.runs_dir
    try:
        record = registry.read_run_record(runs_dir, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    values = await asyncio.to_thread(get_or_build_graph_values, request, run_id)
    best_experiment_path = str(values.get("best_experiment_path") or "").strip()
    if not best_experiment_path:
        raise HTTPException(status_code=409, detail=_NO_BEST_EXPERIMENT_DETAIL)

    experiment_name = Path(best_experiment_path).name
    if not experiment_name:
        # e.g. `best_experiment_path` ends in a trailing "/" -> empty
        # basename. Same "no best experiment yet" 409 as an unset path,
        # rather than a malformed `experiments//submission.csv` message.
        raise HTTPException(status_code=409, detail=_NO_BEST_EXPERIMENT_DETAIL)

    relative_submission = f"experiments/{experiment_name}/{_SUBMISSION_FILENAME}"
    try:
        submission_path, submission_exists = await asyncio.to_thread(
            _resolve_submission_path, record.workspace_path, experiment_name
        )
    except (OSError, ValueError) as exc:
        logger.warning(
            "run %s: failed to resolve submission.csv for experiment %r: %r",
            run_id,
            experiment_name,
            exc,
        )
        raise HTTPException(status_code=409, detail=_NO_BEST_EXPERIMENT_DETAIL) from exc

    if not submission_exists:
        raise HTTPException(
            status_code=409,
            detail=f"submission.csv not found for run {run_id!r}: expected at "
            f"{relative_submission} (resolved to {submission_path})",
        )

    return await _submit_and_score(run_id, record, submission_path, relative_submission)


async def _submit_and_score(
    run_id: str, record: RunRecord, submission_path: Path, relative_submission: str
) -> Response:
    message = f"data-science-lab API submission for run {run_id}"
    try:
        await asyncio.to_thread(
            kaggle_client.submit, record.competition_name, str(submission_path), message
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"invalid Kaggle competition slug {record.competition_name!r}: {exc}",
        ) from exc
    except RuntimeError as exc:
        logger.warning(
            "run %s (%s): Kaggle credentials are not configured: %s",
            run_id,
            record.competition_name,
            exc,
        )
        raise HTTPException(
            status_code=503, detail=f"Kaggle credentials are not configured: {exc}"
        ) from exc
    except Exception as exc:
        logger.exception("run %s (%s): Kaggle submission failed", run_id, record.competition_name)
        raise HTTPException(status_code=502, detail=f"Kaggle submission failed: {exc!r}") from exc

    public_score: float | None = None
    response_message: str | None = None
    try:
        result = await asyncio.to_thread(kaggle_client.get_score, record.competition_name)
        public_score = result["public_score"]
    except TypeError:
        response_message = "Kaggle accepted the submission but has not scored it yet"
    except Exception as exc:
        logger.exception(
            "run %s (%s): failed to read the Kaggle leaderboard score",
            run_id,
            record.competition_name,
        )
        raise HTTPException(
            status_code=502, detail=f"failed to read the Kaggle leaderboard score: {exc!r}"
        ) from exc

    return json_response(
        SubmitResponse(
            public_score=public_score,
            submission_file=relative_submission,
            message=response_message,
        )
    )
