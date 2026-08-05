"""Unit tests for `src.graph.supervisor.supervisor`."""

import pytest

from src.graph.errors import GraphBuilderError
from src.graph.supervisor import supervisor
from src.state import new_state


def _state(phase: str, **overrides):
    state = new_state("comp", "/tmp/comp")
    state["phase"] = phase
    state.update(overrides)
    return state


def test_routes_phase3_at_iteration_zero() -> None:
    state = _state("phase2_research", current_iteration=0)

    assert supervisor(state) == "phase3_baseline"


def test_skips_phase3_after_iteration_zero() -> None:
    state = _state("phase2_research", current_iteration=1)

    assert supervisor(state) == "phase4_design"


def test_routes_to_delivery_when_exhausted() -> None:
    state = _state(
        "phase6_evaluation",
        iterations_without_improvement=5,
        max_iterations=5,
    )

    assert supervisor(state) == "phase7_delivery"


def test_routes_to_delivery_when_past_max() -> None:
    state = _state(
        "phase6_evaluation",
        iterations_without_improvement=6,
        max_iterations=5,
    )

    assert supervisor(state) == "phase7_delivery"


def test_routes_to_design_when_improving() -> None:
    state = _state(
        "phase6_evaluation",
        iterations_without_improvement=1,
        max_iterations=5,
    )

    assert supervisor(state) == "phase4_design"


def test_raises_for_unexpected_phase() -> None:
    state = _state("phase1_understanding")

    with pytest.raises(GraphBuilderError):
        supervisor(state)
