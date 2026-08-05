"""Thin wrapper: build the Phase 5 (Implementation) subgraph from its YAML."""

from langgraph.graph.state import CompiledStateGraph

from src.config.loaders import load_phase_config
from src.graph.phases.generic import ResolveNode, build_phase_subgraph

PHASE_NAME = "phase5_implementation"


def build(resolve_node: ResolveNode) -> CompiledStateGraph:
    return build_phase_subgraph(load_phase_config(PHASE_NAME), resolve_node)
