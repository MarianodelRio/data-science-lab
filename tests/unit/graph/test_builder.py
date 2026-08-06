"""Unit tests for `GraphBuilder.build()`.

Runs against the repo's current real state: `src/nodes/{compute,llm}/base.py`
(T-011/T-010) provide base classes but no concrete node module resolvable by
name — `base` never appears as a node name in any `config/phases/*.yaml`, so
`resolve_node` still falls through to `NoOpNode` for every real node. This
test asserts the whole 7-phase graph still compiles cleanly under that
condition.
"""

from pathlib import Path

import pytest
from langgraph.graph.state import CompiledStateGraph

from src.config.loaders import load_phase_config
from src.config.schema import PhaseConfig
from src.graph import builder as builder_module
from src.graph.builder import GraphBuilder
from src.graph.errors import GraphBuilderError
from src.graph.phases import PHASE_ORDER


def test_no_real_node_modules_exist_yet() -> None:
    """Guards the premise of the other tests in this module: if a future task
    lands a concrete node under `src/nodes/`, this test starts failing and is
    the signal to revisit `NoOpNode`-dependent assumptions here. `base.py`
    (T-010/T-011's `LLMNode`/`ComputeNode` base classes) is excluded — it is
    infrastructure, not itself a node `resolve_node` can ever match by name.
    """
    import src.nodes.compute as compute_pkg
    import src.nodes.llm as llm_pkg

    compute_dir = Path(compute_pkg.__file__).parent
    llm_dir = Path(llm_pkg.__file__).parent
    all_py_files = (*compute_dir.glob("*.py"), *llm_dir.glob("*.py"))
    real_modules = [p for p in all_py_files if p.stem not in ("__init__", "base")]

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


def test_build_raises_when_interrupt_after_is_not_a_real_bool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`PhaseConfig.interrupt_after: bool` is a dataclass type hint, not
    runtime-enforced by the YAML loader. A quoted YAML boolean
    (`interrupt_after: "false"`) would load as the Python string "false",
    and `bool("false")` is `True` — silently giving a phase an unwanted
    human checkpoint with no error anywhere in the load/build/compile path.
    `GraphBuilder.build()` must catch this with a clear error instead. A
    `PhaseConfig` is constructed directly here (no YAML round-trip needed)
    with a non-bool `interrupt_after` to simulate that quoting mistake.
    """
    real_config = load_phase_config("phase1_understanding")
    bad_config = PhaseConfig(
        name=real_config.name,
        nodes=real_config.nodes,
        sequence=real_config.sequence,
        parallel_nodes=real_config.parallel_nodes,
        critic=real_config.critic,
        interrupt_after="false",  # type: ignore[arg-type]  # simulates a quoted YAML bool
    )

    def fake_load_phase_config(stem: str) -> PhaseConfig:
        if stem == "phase1_understanding":
            return bad_config
        return load_phase_config(stem)

    monkeypatch.setattr(builder_module, "load_phase_config", fake_load_phase_config)

    with pytest.raises(GraphBuilderError, match="phase1_understanding"):
        GraphBuilder().build(run_id="t", runs_dir=tmp_path)


def test_build_accepts_real_bool_false_interrupt_after(tmp_path: Path) -> None:
    """Sanity check: a genuine Python `False` (the normal, unquoted YAML
    case) must not raise — guards against the type guard being too strict.
    """
    assert isinstance(load_phase_config("phase2_research").interrupt_after, bool)

    graph = GraphBuilder().build(run_id="t", runs_dir=tmp_path)

    assert "phase2_research" not in graph.interrupt_after_nodes


def test_all_real_phase_yamls_have_bool_interrupt_after() -> None:
    """Guards the premise: none of the 7 real YAMLs trip the new type guard
    today.
    """
    for stem in PHASE_ORDER:
        assert isinstance(load_phase_config(stem).interrupt_after, bool)
