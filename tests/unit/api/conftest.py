"""Shared fixtures for `tests/unit/api/` — a `FakeCompiledGraph`-backed
FastAPI app and `TestClient`, with zero LangGraph/sqlite/network dependency.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import create_app
from tests.fixtures.fake_graph import FakeCompiledGraph


@pytest.fixture
def fake_graph() -> FakeCompiledGraph:
    return FakeCompiledGraph()


@pytest.fixture
def app(tmp_path: Path, fake_graph: FakeCompiledGraph):
    return create_app(runs_dir=tmp_path, graph_factory=lambda *_args, **_kwargs: fake_graph)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    # Must enter the context manager (triggers the ASGI lifespan) so the
    # portal's event loop stays alive across requests within the test — a
    # bare `TestClient(app)` tears its loop down after every call, which
    # cancels any `asyncio.create_task` background work (like the run
    # background task) before it can finish.
    with TestClient(app) as test_client:
        yield test_client
