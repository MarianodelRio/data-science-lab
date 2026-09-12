# ADR 0001 — The run registry lives on disk under `runs/{run_id}/`, not in process memory

**Date:** 2026-09-12
**Status:** Accepted
**Author:** Architect (dev-team)

## Context

T-034 introduces `src/api/` and with it the first thing in the system that has to answer
"which runs exist, and what is each one doing?" (`GET /api/runs`, `GET /api/runs/{id}`,
`POST /api/runs/{id}/resume`).

Nothing in the system answers that today. `LabState` (a protected contract) is per-thread and
holds no run identity, no lifecycle status and no timestamps. The SQLite checkpointer is
**one database per run** (`runs/{run_id}/checkpoint.db`) with no cross-run index. The obvious
shortcut — a module-level `dict[str, RunRecord]` in the API process — satisfies every one of
T-034's acceptance criteria and is wrong:

- design.md's Availability NFR says "process restart acceptable — the SQLite checkpointer
  ensures every run is resumable from last completed node". With an in-memory registry, a run
  created before a restart has a perfectly good checkpoint on disk that no endpoint can reach:
  `GET /api/runs` does not list it, so `POST /api/runs/{id}/resume` is unreachable. The NFR's
  guarantee is silently voided by the API layer.
- design.md's Persistence NFR says "prefer files over memory".
- `runs/{run_id}/` is already a real, shared, multi-writer directory: `src/graph/checkpointer.py`
  writes `checkpoint.db` there and `src/observability/jsonl_callback.py` writes `execution.jsonl`
  there, both taking the same `runs_dir: Path | None = None` test-injection parameter.

## Decision

1. Each run gets a JSON sidecar at `runs/{run_id}/run.json`, written by `src/api/`, holding
   only what `LabState` and the checkpointer cannot hold:

   ```json
   {
     "run_id": "…", "competition_name": "…", "workspace_path": "…",
     "status": "pending|running|interrupted|completed|failed",
     "created_at": "…Z", "updated_at": "…Z"
   }
   ```

   Nothing else. Everything derivable from the checkpoint (`phase`, `current_iteration`,
   `best_score`, …) is read live from `graph.get_state(...)` and never duplicated into the
   sidecar, so the two can never disagree.

2. `GET /api/runs` is a scan of `runs/*/run.json`. The registry is the filesystem; the API holds
   no authoritative in-memory run table. An in-memory map of *live asyncio tasks* is fine and
   expected — it is a cache of the current process's activity, not the source of truth.

3. `runs/` is **not** the ML workspace, so invariant #2 ("WorkspaceManager is the sole file-I/O
   point to the workspace") does not apply to it and `src/api/` writes `run.json` directly. Any
   API access to `~/competitions/{name}/` still goes through `WorkspaceManager`.

4. `src/api/` takes the same `runs_dir: Path | None = None` injection parameter as the two
   existing writers, so tests never touch the real `runs/`.

5. `run_id` is generated server-side (uuid4 hex) and validated as a single path segment before
   any filesystem use — the same rule `src/observability/jsonl_callback.py::_validate_run_id`
   already enforces. A client-supplied id is never trusted into a path.

## Consequences

- A resumed process sees every prior run; the Availability NFR holds end to end instead of
  stopping at the API boundary.
- T-035 (SSE), T-036 (chat), T-037 (submit/MLflow) and T-043 (docker-compose) all inherit one
  answer to "where does run metadata live", and T-043 gains a concrete statement of what must be
  bind-mounted to survive a container restart: `runs/`.
- Cost: a second write path into `runs/{run_id}/` and a directory scan per `GET /api/runs`. At
  single-user scale (design.md Concurrency NFR) the scan is irrelevant.
- Status transitions are now a durable thing that can be wrong — a process killed mid-run leaves
  `status: "running"` on disk. Accepted: `GET /api/runs/{id}` reports live phase from the
  checkpoint, and a stale `running` is recoverable by resume. Reconciling stale status on startup
  is deliberately not in T-034.
