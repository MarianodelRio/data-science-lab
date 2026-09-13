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
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from src.api import registry
from src.api.models import SubmitResponse
from src.api.registry import RunNotFoundError
from src.api.responses import json_response
from src.api.routers.runs import _get_or_build_graph_values as get_or_build_graph_values
from src.tools import kaggle_client
from src.workspace.workspace_manager import WorkspaceManager

router = APIRouter(prefix="/api/runs", tags=["kaggle"])

_SUBMISSION_FILENAME = "submission.csv"
_NO_BEST_EXPERIMENT_DETAIL = "run has no best experiment yet"


@router.post("/{run_id}/submit", response_model=SubmitResponse)
async def submit_run(run_id: str, request: Request) -> Response:
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
    try:
        workspace = WorkspaceManager(record.workspace_path)
        experiment_directory = workspace.experiment_dir(experiment_name)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=_NO_BEST_EXPERIMENT_DETAIL) from exc

    submission_path = experiment_directory / _SUBMISSION_FILENAME
    relative_submission = f"experiments/{experiment_name}/{_SUBMISSION_FILENAME}"
    if not submission_path.is_file():
        raise HTTPException(
            status_code=409,
            detail=f"submission.csv not found for run {run_id!r}: expected at "
            f"{relative_submission} (resolved to {submission_path})",
        )

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
        raise HTTPException(
            status_code=503, detail=f"Kaggle credentials are not configured: {exc}"
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Kaggle submission failed: {exc!r}") from exc

    public_score: float | None = None
    response_message: str | None = None
    try:
        result = await asyncio.to_thread(kaggle_client.get_score, record.competition_name)
        public_score = result["public_score"]
    except TypeError:
        response_message = "Kaggle accepted the submission but has not scored it yet"
    except Exception as exc:
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
