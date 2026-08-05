"""SQLite checkpointer construction.

`runs_dir` defaults to the real repo-root `runs/` directory and exists purely
for test injection — mirrors the `base_dir` convention in
`src/config/loaders.py`/`src/config/paths.py`.

`SqliteSaver.from_conn_string` (the form shown in design.md) is a
`contextmanager`-based generator in the installed `langgraph-checkpoint-sqlite`
version (returns `Iterator[SqliteSaver]`, meant to be used in a `with` block).
That's awkward here: the checkpointer needs to stay alive for the lifetime of
the compiled graph returned by `GraphBuilder.build()`, well past this
function's return. `SqliteSaver(sqlite3.connect(...))` is the always-available
constructor form and doesn't require the caller to manage a context-manager
lifetime, so it's used instead.
"""

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from src.config.paths import REPO_ROOT

RUNS_DIR: Path = REPO_ROOT / "runs"


def build_checkpointer(run_id: str, runs_dir: Path | None = None) -> SqliteSaver:
    """Build a `SqliteSaver` backed by `{runs_dir}/{run_id}/checkpoint.db`.

    Creates the run's directory if it doesn't exist yet. `check_same_thread`
    is disabled because LangGraph may invoke the checkpointer from a different
    thread than the one that created the connection.
    """
    directory = (runs_dir if runs_dir is not None else RUNS_DIR) / run_id
    directory.mkdir(parents=True, exist_ok=True)
    db_path = directory / "checkpoint.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    return SqliteSaver(conn)
