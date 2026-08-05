"""Unit tests for `src.graph.phases.generic.build_phase_subgraph`.

Constructs `PhaseConfig`s directly (no YAML round-trip needed) to exercise
wiring edge cases the 7 real `config/phases/*.yaml` files don't currently
hit: a non-contiguous `parallel_nodes` block, and structural verification of
which node is actually wired as the compiled subgraph's finish point.
"""

from collections.abc import Callable

import pytest

from src.config.schema import PhaseConfig
from src.graph.errors import GraphBuilderError
from src.graph.phases.generic import build_phase_subgraph
from src.state import LabState, new_state


def _fake_resolve_node(name: str) -> Callable[[LabState], dict]:
    def _node(state: LabState) -> dict:
        return {}

    return _node


def _counting_resolve_node(name: str, call_order: list[str]) -> Callable[[LabState], dict]:
    def _node(state: LabState) -> dict:
        call_order.append(name)
        return {}

    return _node


def test_build_phase_subgraph_raises_on_non_contiguous_parallel_nodes() -> None:
    """sequence=[A,B,C,D,E], parallel_nodes=[B,D] — C sits inside the B..D
    span without being a member of parallel_nodes, which would otherwise be
    silently mis-wired as if it were part of the parallel fan-out/fan-in.
    """
    config = PhaseConfig(
        name="non_contiguous_test_phase",
        nodes=("node_a", "node_b", "node_c", "node_d", "node_e"),
        sequence=("node_a", "node_b", "node_c", "node_d", "node_e"),
        parallel_nodes=("node_b", "node_d"),
        critic=None,
        interrupt_after=False,
    )

    with pytest.raises(GraphBuilderError, match="non_contiguous_test_phase"):
        build_phase_subgraph(config, _fake_resolve_node)


def test_build_phase_subgraph_allows_contiguous_parallel_nodes() -> None:
    """Sanity check: a genuinely contiguous parallel block (mirrors phase2's
    real shape) must NOT raise.
    """
    config = PhaseConfig(
        name="contiguous_test_phase",
        nodes=("node_a", "node_b", "node_c", "node_d"),
        sequence=("node_a", "node_b", "node_c", "node_d"),
        parallel_nodes=("node_b", "node_c"),
        critic=None,
        interrupt_after=False,
    )

    build_phase_subgraph(config, _fake_resolve_node)  # must not raise


def test_build_phase_subgraph_finish_point_matches_sequence_last_node() -> None:
    """Structural assertion on the compiled subgraph's actual finish point —
    manual mutation testing found that swapping
    `set_finish_point(sequence[-1])` for `sequence[0]` in generic.py survives
    a smoke test that only checks a passthrough state field. This checks
    LangGraph's own graph introspection: which node has an edge to `__end__`.
    """
    config = PhaseConfig(
        name="finish_point_test_phase",
        nodes=("node_a", "node_b", "node_c"),
        sequence=("node_a", "node_b", "node_c"),
        parallel_nodes=(),
        critic=None,
        interrupt_after=False,
    )

    compiled = build_phase_subgraph(config, _fake_resolve_node)

    edges = compiled.get_graph().edges
    finish_nodes = {edge.source for edge in edges if edge.target == "__end__"}

    assert finish_nodes == {"node_c"}


def test_build_phase_subgraph_last_executed_node_matches_sequence_last_node() -> None:
    """Complementary runtime check to the structural test above: instrument
    every node with a call-order list and assert the last node to actually
    execute in a full run is `sequence[-1]`.
    """
    config = PhaseConfig(
        name="finish_point_runtime_test_phase",
        nodes=("node_a", "node_b", "node_c"),
        sequence=("node_a", "node_b", "node_c"),
        parallel_nodes=(),
        critic=None,
        interrupt_after=False,
    )

    call_order: list[str] = []
    compiled = build_phase_subgraph(config, lambda name: _counting_resolve_node(name, call_order))

    state = new_state("comp", "/tmp/comp")
    compiled.invoke(state)

    assert call_order[-1] == "node_c"
