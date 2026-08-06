"""Integration-style regression tests for `JsonlCallbackHandler` against a real
LangGraph graph that mirrors `src/graph/builder.py`'s exact topology:
`GraphBuilder._wrap_phase` compiles each phase as its own subgraph, then wraps
it in a plain function that calls `compiled.invoke(state)` (no `config`
forwarded) and stamps `phase=stem` onto the *returned* delta.

These graphs are built directly with `langgraph.graph.StateGraph`, not
`GraphBuilder`, to stay fast and dependency-free (no `config/phases/*.yaml`,
no `node_resolver`) while reproducing the exact callback-propagation shape
that caused the two bugs these tests guard against:

- `phase` staleness: `LabState["phase"]` is only stamped *after* a phase
  subgraph finishes, so a naive `inputs.get("phase")` read at `on_chain_start`
  time for a node running inside `phase2_research` would report the leftover
  `phase1_understanding` value.
- Spurious `"LangGraph"`-named lines: both the outer graph's own `.invoke()`
  and each phase subgraph's own top-level `.invoke()` inside `_wrap_phase`
  fire `on_chain_start`/`on_chain_end` too, generically named `"LangGraph"` by
  LangChain — these are not real registered graph nodes and must not produce
  log lines.
"""

import json
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.graph import END, StateGraph

from src.observability.jsonl_callback import JsonlCallbackHandler


def _wrap_phase(stem, compiled):
    """Mirrors `src.graph.builder._wrap_phase` exactly: `compiled.invoke(state)`
    with no `config` kwarg forwarded, then stamps `phase=stem` on the delta."""

    def _run(state):
        result = compiled.invoke(state)
        return {**result, "phase": stem}

    return _run


def _make_phase_subgraph(node_name: str):
    """A one-node subgraph, mirroring `build_phase_subgraph`'s shape for a
    phase with a single agent/compute node."""
    inner = StateGraph(dict)

    def node(state: dict) -> dict:
        return {"messages": [*state.get("messages", []), f"ran:{node_name}"]}

    inner.add_node(node_name, node)
    inner.set_entry_point(node_name)
    inner.add_edge(node_name, END)
    return inner.compile()


def _build_two_phase_graph():
    """Outer graph with two phases in sequence, each wrapping one inner node —
    mirrors `GraphBuilder.build()`'s wiring for a `phase1 -> phase2` slice."""
    outer = StateGraph(dict)
    outer.add_node(
        "phase1_understanding",
        _wrap_phase("phase1_understanding", _make_phase_subgraph("problem_framer")),
    )
    outer.add_node(
        "phase2_research", _wrap_phase("phase2_research", _make_phase_subgraph("researcher"))
    )
    outer.set_entry_point("phase1_understanding")
    outer.add_edge("phase1_understanding", "phase2_research")
    outer.add_edge("phase2_research", END)
    return outer.compile()


def _read_lines(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def test_phase_reflects_currently_executing_phase_not_the_previous_one(tmp_path: Path) -> None:
    """Bug 1 regression: a node running inside `phase2_research`'s subgraph
    must log `phase=phase2_research`, never the stale `phase1_understanding`
    left over from before that phase's subgraph invocation returns."""
    app = _build_two_phase_graph()
    handler = JsonlCallbackHandler("phase-correctness-run", runs_dir=tmp_path)
    initial_state = {
        "messages": [HumanMessage(content="hi")],
        "current_iteration": 0,
        "phase": "phase1_understanding",
    }

    app.invoke(initial_state, config={"callbacks": [handler]})

    lines = _read_lines(handler._log_path)
    by_node = {line["node"]: line["phase"] for line in lines}

    assert by_node["problem_framer"] == "phase1_understanding"
    assert by_node["researcher"] == "phase2_research"
    # The phase-wrapper nodes themselves report their own phase correctly too.
    assert by_node["phase1_understanding"] == "phase1_understanding"
    assert by_node["phase2_research"] == "phase2_research"


def test_no_spurious_langgraph_named_lines_and_one_pair_per_real_node(tmp_path: Path) -> None:
    """Bug 2 regression: the outer graph's own `.invoke()` and each phase
    subgraph's own top-level `.invoke()` inside `_wrap_phase` must not produce
    any log lines — only genuine registered graph nodes get a start/end pair."""
    app = _build_two_phase_graph()
    handler = JsonlCallbackHandler("node-filter-run", runs_dir=tmp_path)
    initial_state = {
        "messages": [HumanMessage(content="hi")],
        "current_iteration": 0,
        "phase": "phase1_understanding",
    }

    app.invoke(initial_state, config={"callbacks": [handler]})

    lines = _read_lines(handler._log_path)
    nodes_logged = [line["node"] for line in lines]

    assert "LangGraph" not in nodes_logged
    assert "unknown" not in nodes_logged

    # Exactly 4 real nodes (2 phase wrappers + 2 inner nodes), one start/end
    # pair each -> exactly 8 lines total, not 8 real + 3 spurious ("LangGraph"
    # x1 outer + x2 phase-subgraph-internal invokes) = 11.
    assert len(lines) == 8
    expected_nodes = {"phase1_understanding", "problem_framer", "phase2_research", "researcher"}
    assert set(nodes_logged) == expected_nodes
    for node in expected_nodes:
        assert nodes_logged.count(node) == 2  # one start + one end


def test_nested_inner_node_is_still_logged_with_correct_phase(tmp_path: Path) -> None:
    """The real inner node (one level deeper than the phase-wrapper node) must
    still be logged — filtering spurious `"LangGraph"` runs must not also
    swallow genuine nested node executions."""
    inner_graph = StateGraph(dict)

    def inner_node(state: dict) -> dict:
        return {"messages": [*state.get("messages", []), "ran"]}

    inner_graph.add_node("solution_architect", inner_node)
    inner_graph.set_entry_point("solution_architect")
    inner_graph.add_edge("solution_architect", END)
    compiled_inner = inner_graph.compile()

    outer = StateGraph(dict)
    outer.add_node("phase4_design", _wrap_phase("phase4_design", compiled_inner))
    outer.set_entry_point("phase4_design")
    outer.add_edge("phase4_design", END)
    app = outer.compile()

    handler = JsonlCallbackHandler("nested-node-run", runs_dir=tmp_path)
    initial_state = {
        "messages": [HumanMessage(content="hi")],
        "current_iteration": 1,
        "phase": "phase3_baseline",
    }
    app.invoke(initial_state, config={"callbacks": [handler]})

    lines = _read_lines(handler._log_path)
    by_node = {line["node"]: line["phase"] for line in lines}

    assert set(by_node) == {"phase4_design", "solution_architect"}
    assert by_node["solution_architect"] == "phase4_design"
    assert by_node["phase4_design"] == "phase4_design"
