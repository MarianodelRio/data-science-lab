"""FastAPI app factory + uvicorn entrypoint.

`create_app`'s `graph_factory` parameter is the injection seam that lets unit
tests substitute a fake compiled graph (see `tests/fixtures/fake_graph.py`)
with no LangGraph/sqlite dependency — `_default_graph_factory` only imports
`src.graph.builder.GraphBuilder` lazily, inside the function body, so that
import (and its transitive LangGraph/sqlite deps) never happens for a test
that injects its own factory.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import FastAPI

from src.api.routers.runs import router as runs_router
from src.config.paths import REPO_ROOT


class CompiledGraphLike(Protocol):
    """The subset of `CompiledStateGraph`'s interface the API layer relies
    on — small enough that a fake test double can implement it directly."""

    def invoke(self, input: Any, config: dict) -> Any: ...
    def get_state(self, config: dict) -> Any: ...
    def update_state(self, config: dict, values: dict) -> Any: ...


GraphFactory = Callable[[str, "Path | None"], CompiledGraphLike]


def _default_graph_factory() -> GraphFactory:
    # Local import: keeps GraphBuilder's LangGraph/sqlite deps out of every
    # test that injects a fake factory instead of calling this function.
    from src.graph.builder import GraphBuilder

    # `CompiledStateGraph`'s real `invoke`/`get_state`/`update_state` accept a
    # `RunnableConfig` and extra optional kwargs — a structural superset of
    # `CompiledGraphLike`'s narrower, test-friendly `dict`-based signatures.
    # It satisfies the protocol at runtime; the cast tells mypy so at this
    # one seam where the real LangGraph type meets our narrower interface.
    return cast(GraphFactory, GraphBuilder().build)


def create_app(runs_dir: Path | None = None, graph_factory: GraphFactory | None = None) -> FastAPI:
    """Build the FastAPI app.

    `runs_dir` and `graph_factory` are injectable so tests can point the API
    at a `tmp_path` and a fake graph, with zero LangGraph/sqlite/network
    dependency. `app.state.active_runs` is a process-local cache of live
    background tasks (never the source of truth — the on-disk registry is)
    used only to reject a second concurrent run with 409.
    """
    app = FastAPI(title="Data Science Lab API")
    app.state.runs_dir = runs_dir if runs_dir is not None else REPO_ROOT / "runs"
    app.state.graph_factory = graph_factory or _default_graph_factory()
    active_runs: dict[str, asyncio.Task] = {}
    app.state.active_runs = active_runs
    app.include_router(runs_router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
