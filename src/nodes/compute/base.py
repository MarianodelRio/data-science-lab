"""Base class for pure-Python (non-LLM) pipeline nodes.

Convention (docs/pipeline.md "Node classification" / "Graph topology" ->
"Node-module convention"): a node lives at src/nodes/compute/{name}.py and
exposes exactly one class defined in that module with a plain `name` class
attribute equal to the module's filename stem, a no-argument constructor,
and `__call__(self, state: LabState) -> dict`. `resolve_node`
(src/graph/node_resolver.py) instantiates every node with `cls()` -- no
arguments -- so `ComputeNode` must not require anything at construction
time.

Unlike `LLMNode` (src/nodes/llm/base.py, T-010), `ComputeNode` has no model,
no AgentConfig, no prompt, no context trimming -- it never imports from
src/llm or any langchain package. Its only I/O dependency is
`WorkspaceManager`, built lazily per-call from state["workspace_path"].
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager


class ComputeNode(ABC):
    """Base class for src/nodes/compute/{name}.py nodes.

    Subclasses must declare `name` as a plain class attribute (matching the
    module's filename stem) and implement `run`. `ComputeNode` deliberately
    does not set `name` itself -- only a concrete subclass may match
    `_find_node_class`'s lookup -- and is itself uninstantiable
    (`run` is abstract) so a landed compute node that forgets to implement
    `run` fails loudly at `cls()` time inside `resolve_node`, not silently.
    """

    name: str

    def workspace(self, state: LabState) -> WorkspaceManager:
        """Build a WorkspaceManager rooted at state["workspace_path"].

        Constructed fresh per call (there is no __init__ that could cache
        it -- nodes are built via cls()) -- this is the sanctioned way a
        ComputeNode subclass touches the workspace filesystem, per
        WorkspaceManager being the sole file-I/O point.
        """
        return WorkspaceManager(state["workspace_path"])

    @abstractmethod
    def run(self, state: LabState) -> dict:
        """Compute and return a partial LabState update. Pure Python only."""
        raise NotImplementedError

    def __call__(self, state: LabState) -> dict:
        """LangGraph node entrypoint -- the shape resolve_node requires."""
        return self.run(state)
