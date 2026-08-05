"""Thin wrapper: build the Phase 1 (Understanding) subgraph from its YAML.

Kept intentionally minimal — this module is on design.md's "Critical modules"
list (>=85% coverage target); the critic-retry logic that phase1's YAML
implies stays internal to `analysis_critic` itself (see context/decisions.md,
T-009), not here.
"""

from langgraph.graph.state import CompiledStateGraph

from src.config.loaders import load_phase_config
from src.graph.phases.generic import ResolveNode, build_phase_subgraph

PHASE_NAME = "phase1_understanding"


def build(resolve_node: ResolveNode) -> CompiledStateGraph:
    return build_phase_subgraph(load_phase_config(PHASE_NAME), resolve_node)
