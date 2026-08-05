"""`GraphBuilder`: assembles the 7 phase subgraphs from `config/phases/*.yaml`
into the single top-level LangGraph `StateGraph`, wires the supervisor's
conditional edges, and attaches the SQLite checkpointer.

See design.md § LangGraph topology for the main-graph diagram this mirrors.
"""

import importlib
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.config.loaders import load_phase_config
from src.config.schema import PhaseConfig
from src.graph import node_resolver
from src.graph.checkpointer import build_checkpointer
from src.graph.errors import GraphBuilderError
from src.graph.phases import PHASE_ORDER
from src.graph.phases.generic import ResolveNode
from src.graph.supervisor import supervisor
from src.state import LabState

# Fixed, non-conditional transitions — design.md's main graph minus the two
# supervisor-routed branches (phase2_research -> ..., phase6_evaluation -> ...).
_FIXED_EDGES: tuple[tuple[str, str], ...] = (
    (START, "phase1_understanding"),
    ("phase1_understanding", "phase2_research"),
    ("phase3_baseline", "phase4_design"),
    ("phase4_design", "phase5_implementation"),
    ("phase5_implementation", "phase6_evaluation"),
    ("phase7_delivery", END),
)


def _load_phase_build_fn(stem: str) -> Callable[[ResolveNode], CompiledStateGraph]:
    module: ModuleType = importlib.import_module(f"src.graph.phases.{stem}")
    build_fn: Callable[[ResolveNode], CompiledStateGraph] = module.build
    return build_fn


def _wrap_phase(stem: str, compiled: CompiledStateGraph) -> Callable[[LabState], dict]:
    """Wrap a compiled phase subgraph so its node function also stamps
    `phase=stem` into the returned state delta — this is how `supervisor`
    (running at the top level) learns which phase just finished. `phase`'s
    write-ownership was previously undefined (see docs/pipeline.md); this
    task establishes it here.
    """

    def _run(state: LabState) -> dict:
        result = compiled.invoke(state)
        return {**result, "phase": stem}

    return _run


class GraphBuilder:
    """Builds the fully wired, compiled top-level pipeline graph for one run."""

    def build(self, run_id: str, runs_dir: Path | None = None) -> CompiledStateGraph:
        graph = StateGraph(LabState)

        configs: dict[str, PhaseConfig] = {}
        for stem in PHASE_ORDER:
            config = load_phase_config(stem)
            configs[stem] = config
            build_fn = _load_phase_build_fn(stem)
            # Resolved dynamically through the `node_resolver` module (not a
            # locally bound name) so tests can monkeypatch
            # `node_resolver.resolve_node` and have it take effect here.
            compiled = build_fn(node_resolver.resolve_node)
            # See the matching comment in src/graph/phases/generic.py — same
            # mypy/langgraph-stub limitation with Callable-typed (vs. literal
            # `def`) node values in generic Protocol overloads.
            graph.add_node(stem, _wrap_phase(stem, compiled))  # type: ignore[call-overload]

        for source, target in _FIXED_EDGES:
            graph.add_edge(source, target)

        graph.add_conditional_edges(
            "phase2_research",
            supervisor,
            {"phase3_baseline": "phase3_baseline", "phase4_design": "phase4_design"},
        )
        graph.add_conditional_edges(
            "phase6_evaluation",
            supervisor,
            {"phase4_design": "phase4_design", "phase7_delivery": "phase7_delivery"},
        )

        for stem in PHASE_ORDER:
            value = configs[stem].interrupt_after
            # `PhaseConfig.interrupt_after: bool` is a dataclass type hint,
            # not runtime-enforced by the YAML loader (owned by
            # src/config/, outside this task's scope). A quoted YAML boolean
            # (`interrupt_after: "false"`) would load as the Python string
            # "false", and `bool("false")` is `True` — a truthy check alone
            # would silently give a phase an unwanted human checkpoint.
            # `isinstance(value, bool)` (not just `type(value) is bool`) is
            # correct here: `bool` has no subclasses in practice, and this
            # check exists to catch non-bool values (str, int, None), not to
            # exclude a bool subclass.
            if not isinstance(value, bool):
                raise GraphBuilderError(
                    f"Phase '{stem}': interrupt_after must be a bool, got "
                    f"{value!r} ({type(value).__name__})"
                )
        interrupt_after = [stem for stem in PHASE_ORDER if configs[stem].interrupt_after]
        checkpointer = build_checkpointer(run_id, runs_dir)

        return graph.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)
