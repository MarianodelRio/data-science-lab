"""`/api/runs/{id}/experiments` — the run's `LabState.experiments` list plus
baseline score, for the frontend `ExperimentsTable` component.

Reads the live checkpoint state via
`src.api.routers.runs._get_or_build_graph_values` — the same cross-router
import `src.api.routers.kaggle` already uses, rather than a second, parallel
helper module.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from src.api import registry
from src.api.models import ExperimentsResponse
from src.api.registry import RunNotFoundError
from src.api.responses import json_response
from src.api.routers.runs import _get_or_build_graph_values as get_or_build_graph_values

router = APIRouter(prefix="/api/runs", tags=["experiments"])


@router.get("/{run_id}/experiments", response_model=ExperimentsResponse)
async def get_experiments(run_id: str, request: Request) -> Response:
    runs_dir: Path = request.app.state.runs_dir
    try:
        # Existence check only — the record itself is unused. Only this call
        # (not `_get_or_build_graph_values`) raises for an unknown run_id;
        # the latter would silently return `{}` for one.
        registry.read_run_record(runs_dir, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    values: dict[str, Any] = await asyncio.to_thread(get_or_build_graph_values, request, run_id)
    baseline_results_path = str(values.get("baseline_results_path") or "").strip()
    baseline_score = values.get("baseline_score") if baseline_results_path else None
    return json_response(
        ExperimentsResponse(
            experiments=values.get("experiments") or [],
            baseline_score=baseline_score,
            best_experiment_path=values.get("best_experiment_path", ""),
        )
    )
