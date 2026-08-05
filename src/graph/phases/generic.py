"""Generic phase-subgraph assembly, driven entirely by `PhaseConfig`.

Every one of the 7 `src/graph/phases/phase{N}_{name}.py` wrapper modules calls
this with its own loaded `PhaseConfig` — the actual wiring logic lives here
once, not duplicated per phase.
"""

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.config.schema import PhaseConfig
from src.state import LabState

ResolveNode = Callable[[str], Callable[[LabState], dict]]


def build_phase_subgraph(config: PhaseConfig, resolve_node: ResolveNode) -> CompiledStateGraph:
    """Assemble and compile a single phase's subgraph from its `PhaseConfig`.

    - Every name in `config.nodes` becomes a graph node via `resolve_node`.
    - With no `parallel_nodes`, `config.sequence` is chained pairwise.
    - With `parallel_nodes` set, the maximal contiguous run of parallel nodes
      inside `sequence` fans out from the node immediately before the run
      (or `START` if the run starts at `sequence[0]`) and fans back in to the
      node immediately after the run (or `END` if the run ends at
      `sequence[-1]`); the rest of `sequence` chains normally.
    - Entry/finish points are `sequence[0]`/`sequence[-1]`.

    Subgraph-level `compile()` takes no checkpointer/interrupts — those apply
    only once, at the top level, in `GraphBuilder.build()`.
    """
    graph = StateGraph(LabState)
    for name in config.nodes:
        # mypy cannot match a `Callable[[LabState], dict]`-*typed* value (as
        # opposed to a literal `def`) against `add_node`'s generic Protocol
        # overloads — a known mypy/langgraph-stub limitation, not a real type
        # error (confirmed structurally correct by the test suite at runtime).
        graph.add_node(name, resolve_node(name))  # type: ignore[call-overload]

    sequence = config.sequence
    parallel_set = set(config.parallel_nodes)

    # Intentional pairwise-consecutive zip: `sequence[1:]` is always exactly
    # one shorter than `sequence`, so `strict=True` would be wrong here.
    for a, b in zip(sequence, sequence[1:]):  # noqa: B905
        if a in parallel_set or b in parallel_set:
            continue
        graph.add_edge(a, b)

    if parallel_set:
        run_indices = [i for i, node_name in enumerate(sequence) if node_name in parallel_set]
        start, end = run_indices[0], run_indices[-1]
        before = sequence[start - 1] if start > 0 else START
        after = sequence[end + 1] if end + 1 < len(sequence) else END
        for node_name in sequence[start : end + 1]:
            graph.add_edge(before, node_name)
            graph.add_edge(node_name, after)

    graph.set_entry_point(sequence[0])
    graph.set_finish_point(sequence[-1])
    return graph.compile()
