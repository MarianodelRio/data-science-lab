"""Pydantic v2 request/response models for the run management endpoints.

`best_score` in `RunSummary` stays a real, typed `float` — it keeps the
genuine `float("-inf")` seeded by `src.state.new_state` until the first
experiment improves on it. `ser_json_inf_nan="null"` fixes the problem at the
serialization mechanism instead: it makes Pydantic's own JSON serializer
(`model_dump_json()` / `TypeAdapter.dump_json()`) emit valid JSON `null` for
`-inf` rather than the invalid `-Infinity` token that plain `json.dumps` (and
FastAPI's default `jsonable_encoder` response path) would produce. Routers
must call `model_dump_json()`/`dump_json()` directly — see
`src/api/routers/runs.py::_json`/`_json_list` — never rely on
`response_model=` to serialize the actual response body.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunCreateRequest(BaseModel):
    """Body of `POST /api/runs`. `run_id` is deliberately absent — it is
    always server-generated (see `routers/runs.py::create_run`)."""

    competition_name: str = Field(min_length=1)
    workspace_path: str = Field(min_length=1)
    max_iterations: int = Field(default=10, gt=0)


class RunCreateResponse(BaseModel):
    run_id: str
    status: str


class RunSummary(BaseModel):
    """A run's registry fields plus live `phase`/`current_iteration`/
    `best_score` read from the LangGraph checkpoint."""

    model_config = ConfigDict(ser_json_inf_nan="null")

    run_id: str
    competition_name: str
    workspace_path: str
    status: str
    phase: str
    current_iteration: int
    best_score: float
    created_at: str
    updated_at: str


class ResumeRequest(BaseModel):
    """Body of `POST /api/runs/{id}/resume`. An empty string is a legitimate
    "proceed, no comment" value, not an invalid one."""

    feedback: str


class ResumeResponse(BaseModel):
    run_id: str
    status: str


class SubmitResponse(BaseModel):
    """Response of `POST /api/runs/{id}/submit`. `public_score` is `None` when
    Kaggle has accepted the submission but not yet scored it — the normal
    state in the seconds-to-minutes window right after submitting (see
    `routers/kaggle.py`), not an error."""

    model_config = ConfigDict(ser_json_inf_nan="null")

    public_score: float | None
    submission_file: str
    message: str | None


class MlflowUrlResponse(BaseModel):
    url: str


class ExperimentsResponse(BaseModel):
    """Response of `GET /api/runs/{id}/experiments`.

    `experiments` passes `LabState.experiments` through unchanged — ids are
    unique by construction, so no server-side rewriting/dedup happens here.
    `baseline_score` is `null` until `baseline_results_path` is set, never
    the raw `0.0` seed `LabState` starts with. `ser_json_inf_nan="null"`
    guards not just `baseline_score` but also a corrupted/failed
    experiment's `cv_score` inside `experiments`, which can be `inf`/`nan`.
    """

    model_config = ConfigDict(ser_json_inf_nan="null")

    experiments: list[dict[str, Any]]
    baseline_score: float | None
    best_experiment_path: str
