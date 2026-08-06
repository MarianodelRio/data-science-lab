"""Unit tests for `GraphBuilder.build()`.

`GraphBuilder.build()` wires up the 7-phase graph via `build_phase_subgraph`,
which calls `graph.add_node(name, resolve_node(name))` for every node in
every phase's `sequence` — `resolve_node(name)` runs, and any real `LLMNode`
it resolves to gets *constructed*, eagerly at build/compile time (not lazily
at invoke time). `LLMNode.__init__` reads `Settings.load()` and
`LLMFactory.get(role)`, which needs `config/settings.yaml`'s five
`${...}`-interpolated env vars to be set — so ever since a first concrete
node landed under `src/nodes/llm/` (`data_analyst`, T-013), plain
`GraphBuilder().build()` needs those env vars present even though these
tests never call `.invoke()` and so never make a real LLM network call
(`ChatOpenAI`/`ChatAnthropic`/`ChatGroq` construction itself doesn't touch
the network — only `.invoke()` would). See the module-level `_fake_api_keys`
fixture below. Tests that actually *run* a phase (and so also need the LLM
call itself mocked) live in `tests/unit/graph/test_checkpointer.py` and
`tests/integration/phases/test_phase_subgraphs_smoke.py`.
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
from src.llm.factory import LLMFactory


@pytest.fixture(autouse=True)
def _fake_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GROQ_API_KEY",
        "KAGGLE_USERNAME",
        "KAGGLE_KEY",
    ):
        monkeypatch.setenv(var, "fake-value-for-settings-load")
    # Mirrors tests/unit/llm/test_factory.py's `reset_factory_cache` fixture:
    # `LLMFactory._settings` is a real class-level cache (not itself mocked
    # here, since real node construction at build time is exactly what these
    # tests exercise) — reset it so this file's fake values never leak into,
    # or get clobbered by, other test files' real `LLMFactory.get()` calls.
    LLMFactory._settings = None
    yield
    LLMFactory._settings = None


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
