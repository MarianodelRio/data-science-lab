"""`/api/runs/{id}/files/{path}` — serve a workspace file's text content, for
the frontend `FileViewer` component.

Only `registry` (for `workspace_path`) and `WorkspaceManager` are needed here
— no graph interaction. `WorkspaceManager.read_text` (via `_resolve`) rejects
empty/`.`, absolute, and `..`-traversal paths with `ValueError`, which is
mapped to `400` below.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from src.api import registry
from src.api.registry import RunNotFoundError
from src.workspace.workspace_manager import WorkspaceManager

router = APIRouter(prefix="/api/runs", tags=["files"])


def _read_workspace_text(workspace_path: str, relative_path: str) -> str:
    """Construct `WorkspaceManager` (blocking `mkdir`), resolve, and read the
    file as one blocking unit — mirrors `kaggle.py::_resolve_submission_path`'s
    rationale exactly, run via `asyncio.to_thread` in the caller."""
    return WorkspaceManager(workspace_path).read_text(relative_path)


@router.get("/{run_id}/files/{path:path}")
async def get_file(run_id: str, path: str, request: Request) -> PlainTextResponse:
    runs_dir: Path = request.app.state.runs_dir
    try:
        record = registry.read_run_record(runs_dir, run_id)
    except RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        content = await asyncio.to_thread(_read_workspace_text, record.workspace_path, path)
    except UnicodeDecodeError as exc:
        # Must be caught before `ValueError` below: `UnicodeDecodeError` is a
        # `ValueError` subclass, so this branch would otherwise never run.
        raise HTTPException(status_code=415, detail=f"file is not valid UTF-8 text: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return PlainTextResponse(content)
