"""On-disk run registry.

Per ADR-0001, the run registry lives at `{runs_dir}/{run_id}/run.json` — one
file per run holding only what is not derivable from the LangGraph checkpoint
(`run_id`, `competition_name`, `workspace_path`, `status`, `created_at`,
`updated_at`). `GET /api/runs` is effectively a scan of `runs/*/run.json`.

This module has no dependency on `src/graph/` — it only ever reads/writes
plain JSON files with `pathlib`/`json`, mirroring the convention already used
by `src/graph/checkpointer.py` and `src/observability/jsonl_callback.py` for
their own `runs/{run_id}/...` files. `WorkspaceManager` is not used here: it
is scoped to the generated ML workspace, not the agent system's own `runs/`
directory.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_RUN_RECORD_FILENAME = "run.json"


class RunNotFoundError(Exception):
    """Raised when a run_id is invalid or has no on-disk record."""


def validate_run_id(run_id: str) -> None:
    """Validate `run_id` as a safe, single path segment.

    Mirrors `src/observability/jsonl_callback.py::_validate_run_id` exactly
    (non-empty, not "." or "..", no "/", "\\", or ".." substring) so a
    malicious or malformed run_id can never escape `runs_dir`.
    """
    if not run_id or run_id in {".", ".."}:
        raise ValueError(f"run_id must be a non-empty path segment, got {run_id!r}")
    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise ValueError(f"run_id must not contain path separators or '..': {run_id!r}")


@dataclass
class RunRecord:
    """The on-disk fields for one run — nothing derivable from the LangGraph
    checkpoint is duplicated here (see ADR-0001)."""

    run_id: str
    competition_name: str
    workspace_path: str
    status: str  # pending|running|interrupted|completed|failed
    created_at: str  # ISO 8601 UTC
    updated_at: str  # ISO 8601 UTC

    def to_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_dir(runs_dir: Path, run_id: str) -> Path:
    return runs_dir / run_id


def _record_path(runs_dir: Path, run_id: str) -> Path:
    return _run_dir(runs_dir, run_id) / _RUN_RECORD_FILENAME


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write `data` as JSON to `path` atomically (tmp-file + `os.replace`), so
    a concurrent reader never observes a torn/partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp_path, path)


def create_run_record(
    runs_dir: Path, run_id: str, competition_name: str, workspace_path: str
) -> RunRecord:
    """Create and persist a new run record with status `"pending"`."""
    validate_run_id(run_id)
    now = _now_iso()
    record = RunRecord(
        run_id=run_id,
        competition_name=competition_name,
        workspace_path=workspace_path,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    _atomic_write_json(_record_path(runs_dir, run_id), record.to_dict())
    return record


def read_run_record(runs_dir: Path, run_id: str) -> RunRecord:
    """Read the run record for `run_id`.

    An invalid `run_id` format and a missing `run.json` both raise
    `RunNotFoundError` — callers (the API routers) only ever need to catch
    one exception type to produce a 404.
    """
    try:
        validate_run_id(run_id)
    except ValueError as exc:
        raise RunNotFoundError(f"invalid run_id: {run_id!r}") from exc

    path = _record_path(runs_dir, run_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RunNotFoundError(f"no run found for run_id: {run_id!r}") from exc

    try:
        data = json.loads(raw)
        return RunRecord(**data)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        raise RunNotFoundError(f"run record for {run_id!r} is corrupt") from exc


def update_run_status(runs_dir: Path, run_id: str, status: str) -> RunRecord:
    """Update `status` and `updated_at` on an existing run record."""
    record = read_run_record(runs_dir, run_id)
    record.status = status
    record.updated_at = _now_iso()
    _atomic_write_json(_record_path(runs_dir, run_id), record.to_dict())
    return record


def list_run_records(runs_dir: Path) -> list[RunRecord]:
    """List all run records under `runs_dir`.

    Returns `[]` if `runs_dir` doesn't exist yet. A `run.json` that fails to
    parse is skipped (with a warning logged) rather than 500ing the whole
    listing — one corrupt run must not take down the run list for everyone
    else.
    """
    if not runs_dir.is_dir():
        return []

    records: list[RunRecord] = []
    for run_json_path in sorted(runs_dir.glob(f"*/{_RUN_RECORD_FILENAME}")):
        run_id = run_json_path.parent.name
        try:
            data = json.loads(run_json_path.read_text(encoding="utf-8"))
            records.append(RunRecord(**data))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("skipping unparseable run record run_id=%r: %r", run_id, exc)
    return records
