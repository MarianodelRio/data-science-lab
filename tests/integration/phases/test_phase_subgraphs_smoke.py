"""Integration smoke test: each of the 7 phase subgraphs, built standalone via
its `build(resolve_node)`, compiles and runs one full pass over a fresh
`new_state(...)` without raising.

Recommended by design.md's testing-strategy row for `src/graph/` — real
end-to-end coverage of the parallel fan-out/fan-in wiring (phase2) in
addition to the plain sequential phases, all with `NoOpNode` placeholders
since no real node implementations exist yet.
"""

import importlib

import pytest

from src.graph.node_resolver import resolve_node
from src.graph.phases import PHASE_ORDER
from src.state import new_state


@pytest.mark.parametrize("stem", PHASE_ORDER)
def test_phase_subgraph_compiles_and_runs(stem: str) -> None:
    module = importlib.import_module(f"src.graph.phases.{stem}")
    compiled = module.build(resolve_node)

    state = new_state("comp", "/tmp/comp")
    result = compiled.invoke(state)

    assert result["competition_name"] == "comp"
