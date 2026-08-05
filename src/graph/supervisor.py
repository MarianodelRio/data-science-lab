"""Supervisor: the pipeline's only conditional-edge routing logic.

Pure Python, deterministic, no LLM — see design.md § LangGraph topology. The
two conditional edges in the main graph (out of `phase2_research` and out of
`phase6_evaluation`) both call this single function; `GraphBuilder` picks
which branch key is legal at each call site via `path_map`.

Critic-retry and specialist-dispatch control flow are deliberately NOT
supervisor concerns — see context/decisions.md (T-009) for why those stay
internal to their owning nodes instead of becoming graph-level conditional
edges.
"""

from src.graph.errors import GraphBuilderError
from src.state import LabState


def supervisor(state: LabState) -> str:
    """Return the name of the next phase to run.

    - After `phase2_research`: `phase3_baseline` only when `current_iteration
      == 0` (the baseline runs exactly once, per CLAUDE.md invariant #4),
      otherwise straight to `phase4_design`.
    - After `phase6_evaluation`: `phase7_delivery` once
      `iterations_without_improvement >= max_iterations`, otherwise loop back
      to `phase4_design` for another iteration.
    """
    phase = state["phase"]
    if phase == "phase2_research":
        return "phase3_baseline" if state["current_iteration"] == 0 else "phase4_design"
    if phase == "phase6_evaluation":
        if state["iterations_without_improvement"] >= state["max_iterations"]:
            return "phase7_delivery"
        return "phase4_design"
    raise GraphBuilderError(f"supervisor called from unexpected phase: {phase!r}")
