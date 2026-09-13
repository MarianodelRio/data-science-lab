"""Shared response-serialization helper for `src/api/routers/`.

Every router response is built through `json_response` (never
`response_model=`'s default `jsonable_encoder` path), which serializes with
Pydantic's own JSON encoder (`model_dump_json()`). `jsonable_encoder` +
stdlib `json.dumps` would emit the invalid JSON token `-Infinity` for a
`-inf` field (e.g. a fresh run's `best_score`, see `src/api/models.py`).
`response_model=` stays on each decorator purely so the endpoint still shows
up correctly in the generated OpenAPI docs.
"""

from __future__ import annotations

from fastapi import Response
from pydantic import BaseModel


def json_response(model: BaseModel, status_code: int = 200) -> Response:
    """Serialize `model` via Pydantic's own JSON encoder, bypassing
    `jsonable_encoder`/stdlib `json.dumps` (which would emit invalid
    `-Infinity` for a `-inf` field)."""
    return Response(
        content=model.model_dump_json(), media_type="application/json", status_code=status_code
    )
