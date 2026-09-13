"""`/api/mlflow` endpoints — expose the browser-reachable MLflow UI URL.

`app.state.mlflow_url` is resolved once at app construction (see
`create_app` in `src/api/main.py`): `create_app(mlflow_url=...)` param ->
`MLFLOW_PUBLIC_URL` env var -> `http://localhost:5000` default. This module
deliberately never imports `src.config.settings` — `Settings.load()` has no
place on this endpoint's request path.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from src.api.models import MlflowUrlResponse
from src.api.responses import json_response

router = APIRouter(prefix="/api/mlflow", tags=["mlflow"])


@router.get("/url", response_model=MlflowUrlResponse)
async def get_mlflow_url(request: Request) -> Response:
    return json_response(MlflowUrlResponse(url=request.app.state.mlflow_url))
