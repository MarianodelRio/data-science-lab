"""Unit tests for the `LabState` contract and `new_state()` factory.

`src/state.py` is on the critical-modules list (mutation testing runs against
it), so assertions here are precise equality checks, not just truthiness.
"""

import math

import pytest

from src.state import LabState, new_state


def test_new_state_has_every_labstate_key():
    expected_keys = set(LabState.__required_keys__)
    state = new_state("comp", "/tmp/comp")
    assert set(state.keys()) == expected_keys


def test_new_state_echoes_input():
    state = new_state("titanic", "/tmp/titanic")
    assert state["competition_name"] == "titanic"
    assert state["workspace_path"] == "/tmp/titanic"


def test_new_state_control_defaults():
    state = new_state("comp", "/tmp/comp")
    assert state["current_iteration"] == 0
    assert state["iterations_without_improvement"] == 0
    assert state["max_iterations"] == 10
    assert state["phase"] == ""


def test_new_state_best_score_is_negative_infinity():
    state = new_state("comp", "/tmp/comp")
    assert math.isinf(state["best_score"]) and state["best_score"] < 0
    assert state["best_score"] == float("-inf")


def test_new_state_other_scores_are_zero():
    state = new_state("comp", "/tmp/comp")
    assert state["baseline_score"] == 0.0
    assert state["last_score"] == 0.0
    assert state["score_delta"] == 0.0


@pytest.mark.parametrize(
    "field",
    [
        "eda_report_path",
        "problem_definition_path",
        "validation_config_path",
        "baseline_results_path",
        "solution_plan_path",
        "feature_spec_path",
        "best_experiment_path",
    ],
)
def test_new_state_path_fields_empty(field):
    state = new_state("comp", "/tmp/comp")
    assert state[field] == ""


def test_new_state_checkpoint_fields_empty():
    state = new_state("comp", "/tmp/comp")
    assert state["checkpoint_summary"] == ""
    assert state["human_feedback"] == ""


def test_new_state_experiments_and_messages_are_empty_lists():
    state = new_state("comp", "/tmp/comp")
    assert state["experiments"] == []
    assert isinstance(state["experiments"], list)
    assert state["messages"] == []
    assert isinstance(state["messages"], list)


def test_new_state_lists_not_shared_across_calls():
    state_a = new_state("comp", "/tmp/comp")
    state_b = new_state("comp", "/tmp/comp")

    state_a["experiments"].append({"id": "exp-1"})
    state_a["messages"].append("hello")

    assert state_b["experiments"] == []
    assert state_b["messages"] == []


def test_new_state_no_io(monkeypatch):
    def _raise(*args, **kwargs):
        raise AssertionError("new_state() must not perform I/O")

    monkeypatch.setattr("builtins.open", _raise)
    new_state("comp", "/tmp/comp")  # should not raise


def test_labstate_messages_field_uses_add_messages_reducer():
    from typing import get_type_hints

    from langgraph.graph.message import add_messages

    hints = get_type_hints(LabState, include_extras=True)
    assert add_messages in hints["messages"].__metadata__
