"""Node discovery by convention: `GraphBuilder` never imports a node module by
name directly — every node name in a phase YAML is resolved through
`resolve_node`, so parallel node-implementation tasks (T-010, T-011, ...) never
touch a shared registry file.

Convention (documented in docs/pipeline.md "Graph topology"): a node lives at
`src/nodes/{llm|compute}/{name}.py` and exposes exactly one class, defined in
that module, with a `name` class attribute equal to the module's filename
stem, constructible with no arguments, callable as `instance(state) ->
dict`.
"""

import importlib
import inspect
from collections.abc import Callable
from types import ModuleType

from src.graph.errors import GraphBuilderError
from src.graph.nodes_noop import NoOpNode
from src.state import LabState

_NODE_KINDS = ("llm", "compute")


def _find_node_class(module: ModuleType, name: str) -> type:
    """Find the single class defined in `module` (not merely imported into it)
    whose `name` class attribute equals `name`.

    Raises `GraphBuilderError` for zero or more than one match — the module
    exists (so this isn't "not implemented yet"), which makes an ambiguous or
    missing node class a real bug in a landed node.
    """
    matches = [
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj)
        and obj.__module__ == module.__name__
        and getattr(obj, "name", None) == name
    ]
    if len(matches) != 1:
        raise GraphBuilderError(
            f"Expected exactly one class with name='{name}' defined in "
            f"{module.__name__}, found {len(matches)}"
        )
    return matches[0]


def resolve_node(name: str) -> Callable[[LabState], dict]:
    """Resolve a phase-YAML node name to a callable `state -> dict` node.

    Tries `src.nodes.llm.{name}` then `src.nodes.compute.{name}`; the first
    module that imports successfully is used to find the node class. If
    neither module exists yet, falls back to a `NoOpNode` placeholder — this
    is the expected path for every node until its implementing task lands.
    """
    for kind in _NODE_KINDS:
        try:
            module = importlib.import_module(f"src.nodes.{kind}.{name}")
        except ModuleNotFoundError:
            continue
        cls = _find_node_class(module, name)
        return cls()
    return NoOpNode(name)
