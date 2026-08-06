"""Unit tests for `ComputeNode` (src/nodes/compute/base.py) -- the run->delta
lifecycle, WorkspaceManager access, abstractness, and the "no LLM import"
invariant.
"""

from __future__ import annotations

import ast
import inspect

import pytest

import src.nodes.compute.base as base_module
from src.nodes.compute.base import ComputeNode
from src.state import LabState, new_state
from src.workspace.workspace_manager import WorkspaceManager


class _DoubleIterationNode(ComputeNode):
    name = "double_iteration_node_for_test"

    def run(self, state: LabState) -> dict:
        wm = self.workspace(state)
        wm.write_json("marker.json", {"seen_iteration": state["current_iteration"]})
        return {"last_score": float(state["current_iteration"] * 2)}


def test_run_computes_delta_from_state_field(tmp_path):
    node = _DoubleIterationNode()
    state = new_state("titanic", str(tmp_path / "workspace"))
    state["current_iteration"] = 3

    assert node.run(state) == {"last_score": 6.0}


def test_call_delegates_to_run_and_returns_same_delta(tmp_path):
    node = _DoubleIterationNode()

    state_a = new_state("titanic", str(tmp_path / "workspace_a"))
    state_a["current_iteration"] = 5
    delta_from_run = node.run(state_a)

    state_b = new_state("titanic", str(tmp_path / "workspace_b"))
    state_b["current_iteration"] = 5
    delta_from_call = node(state_b)

    assert delta_from_run == delta_from_call


def test_call_writes_through_workspace_manager(tmp_path):
    node = _DoubleIterationNode()
    state = new_state("titanic", str(tmp_path / "workspace"))
    state["current_iteration"] = 7

    node(state)

    wm = WorkspaceManager(state["workspace_path"])
    assert wm.read_json("marker.json") == {"seen_iteration": 7}


def test_computenode_is_abstract_cannot_be_instantiated():
    with pytest.raises(TypeError):
        ComputeNode()  # type: ignore[abstract]


def test_base_module_does_not_import_llm_or_langchain():
    source_path = inspect.getfile(base_module)
    with open(source_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=source_path)

    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    forbidden_prefixes = ("src.llm", "langchain")
    offending = [
        name
        for name in imported_names
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
    ]
    assert offending == []
