"""Unit tests for `GraphBuilder.build()`.

Runs against the repo's current real state: no node implementation modules
exist under `src/nodes/{llm,compute}/` yet (T-010/T-011 are still
`available`), so every node resolves to a `NoOpNode` placeholder — this test
asserts the whole 7-phase graph still compiles cleanly under that condition.
"""

from pathlib import Path

from langgraph.graph.state import CompiledStateGraph

from src.graph.builder import GraphBuilder


def test_no_real_node_modules_exist_yet() -> None:
    """Guards the premise of the other tests in this module: if a future task
    lands a real node under `src/nodes/`, this test starts failing and is the
    signal to revisit `NoOpNode`-dependent assumptions here.
    """
    import src.nodes.compute as compute_pkg
    import src.nodes.llm as llm_pkg

    compute_dir = Path(compute_pkg.__file__).parent
    llm_dir = Path(llm_pkg.__file__).parent
    all_py_files = (*compute_dir.glob("*.py"), *llm_dir.glob("*.py"))
    real_modules = [p for p in all_py_files if p.stem != "__init__"]

    assert real_modules == []


def test_build_returns_compiled_graph_without_raising(tmp_path: Path) -> None:
    graph = GraphBuilder().build(run_id="t", runs_dir=tmp_path)

    assert isinstance(graph, CompiledStateGraph)


def test_build_interrupt_after_matches_expected_phases(tmp_path: Path) -> None:
    graph = GraphBuilder().build(run_id="t", runs_dir=tmp_path)

    assert set(graph.interrupt_after_nodes) == {
        "phase1_understanding",
        "phase4_design",
        "phase6_evaluation",
    }
