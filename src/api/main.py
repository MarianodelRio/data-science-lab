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
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from fastapi import FastAPI

from src.api.routers.chat import router as chat_router
from src.api.routers.events import router as events_router
from src.api.routers.kaggle import router as kaggle_router
from src.api.routers.mlflow import router as mlflow_router
from src.api.routers.runs import router as runs_router
from src.config.paths import REPO_ROOT

logger = logging.getLogger(__name__)

_DEFAULT_MLFLOW_URL = "http://localhost:5000"
_MLFLOW_PUBLIC_URL_ENV_VAR = "MLFLOW_PUBLIC_URL"


class CompiledGraphLike(Protocol):
    """The subset of `CompiledStateGraph`'s interface the API layer relies
    on — small enough that a fake test double can implement it directly."""

    def invoke(self, input: Any, config: dict) -> Any: ...
    def get_state(self, config: dict) -> Any: ...
    def update_state(self, config: dict, values: dict) -> Any: ...


class ExplainerLike(Protocol):
    """The subset of `src.api.explainer.Explainer`'s interface the chat
    router relies on — small enough that a fake test double can implement it
    directly."""

    def answer(self, question: str, *, history: list[dict[str, str]] | None = None) -> str: ...


class ExplainerFactory(Protocol):
    def __call__(
        self,
        *,
        workspace: Any,
        state_reader: Callable[[], dict[str, Any]],
        rag_store: Any | None,
    ) -> ExplainerLike: ...


GraphFactory = Callable[[str, "Path | None"], CompiledGraphLike]
RagStoreFactory = Callable[[str], Any | None]


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


def _default_explainer_factory() -> ExplainerFactory:
    # Local import: keeps this out of every test that injects its own
    # explainer_factory (mirrors `_default_graph_factory`'s rationale).
    from src.api.explainer import Explainer

    # `Explainer.__init__`'s keyword-only signature matches `ExplainerFactory`
    # exactly, so the class itself is a valid factory callable.
    return Explainer


def _default_rag_store_factory() -> RagStoreFactory:
    def _build(competition_name: str) -> Any | None:
        # Local import: `RagStore`'s embedding function can trigger a model
        # download on first use, which must never happen as a side effect of
        # importing this module or running the unit test suite.
        from src.tools.rag import RagStore

        try:
            return RagStore(competition_name)
        except Exception:
            logger.warning(
                "RagStore unavailable for %r; chat will run without RAG", competition_name
            )
            return None

    return _build


def create_app(
    runs_dir: Path | None = None,
    graph_factory: GraphFactory | None = None,
    explainer_factory: ExplainerFactory | None = None,
    rag_store_factory: RagStoreFactory | None = None,
    mlflow_url: str | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    `runs_dir` and `graph_factory` are injectable so tests can point the API
    at a `tmp_path` and a fake graph, with zero LangGraph/sqlite/network
    dependency. `app.state.active_runs` is a process-local cache of live
    background tasks (never the source of truth — the on-disk registry is)
    used only to reject a second concurrent run with 409.
    `app.state.active_submissions` is the analogous per-`run_id` guard for
    `POST /api/runs/{id}/submit` (`src/api/routers/kaggle.py`) — a distinct
    concept (in-flight Kaggle submissions, not pipeline runs) tracked in its
    own collection.

    `explainer_factory` and `rag_store_factory` are the same kind of
    injection seam for the chat WebSocket (`src/api/routers/chat.py`): tests
    inject a factory returning a fake explainer / `None` rag store, with zero
    LLM/network/model-download dependency.

    `mlflow_url` seeds `app.state.mlflow_url` (read by `GET /api/mlflow/url`):
    the param takes precedence over the `MLFLOW_PUBLIC_URL` env var, which
    takes precedence over the `http://localhost:5000` default.
    """
    app = FastAPI(title="Data Science Lab API")
    app.state.runs_dir = runs_dir if runs_dir is not None else REPO_ROOT / "runs"
    app.state.graph_factory = graph_factory or _default_graph_factory()
    app.state.explainer_factory = explainer_factory or _default_explainer_factory()
    app.state.rag_store_factory = rag_store_factory or _default_rag_store_factory()
    resolved_mlflow_url = mlflow_url
    if resolved_mlflow_url is None:
        resolved_mlflow_url = os.environ.get(_MLFLOW_PUBLIC_URL_ENV_VAR)
    if resolved_mlflow_url is None:
        resolved_mlflow_url = _DEFAULT_MLFLOW_URL
    app.state.mlflow_url = resolved_mlflow_url
    active_runs: dict[str, asyncio.Task] = {}
    app.state.active_runs = active_runs
    # Per-run_id in-flight guard for `POST /api/runs/{id}/submit`, checked
    # before any state-mutating work and cleared in a `finally` on every exit
    # path — see `src/api/routers/kaggle.py`.
    active_submissions: set[str] = set()
    app.state.active_submissions = active_submissions
    # Built graphs (and, in the real factory, their underlying sqlite
    # checkpoint connection) are expensive and stateful — cache one per
    # `run_id` so it is built at most once per process lifetime rather than
    # once per request. See `_get_or_build_graph` in `src/api/routers/runs.py`.
    graph_cache: dict[str, Any] = {}
    app.state.graph_cache = graph_cache
    # Per-run SSE event queues, written by `EventEmitter` and read by
    # `routers/events.py`. Registered synchronously in `create_run`/
    # `resume_run` (before the background task starts) and removed lazily
    # once the SSE generator consumes the terminal `STREAM_END` sentinel.
    event_queues: dict[str, asyncio.Queue[Any]] = {}
    app.state.event_queues = event_queues
    app.include_router(runs_router)
    app.include_router(events_router)
    app.include_router(chat_router)
    app.include_router(kaggle_router)
    app.include_router(mlflow_router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
