"""Node discovery by convention: `GraphBuilder` never imports a node module by
name directly — every node name in a phase YAML is resolved through
`resolve_node`, so parallel node-implementation tasks (T-010, T-011, ...) never
touch a shared registry file.

Convention (documented in docs/pipeline.md "Graph topology"): a node lives at
`src/nodes/{llm|compute}/{name}.py` and exposes exactly one class, defined in
that module, with a `name` class attribute equal to the module's filename
stem, constructible with no arguments, callable as `instance(state) ->
dict`. `name` must be a plain class attribute (`name = "..."` at class body
level) — a Pydantic v2 `BaseModel` subclass declaring `name: str = "..."` as
a typed field does NOT satisfy this: Pydantic v2 field defaults aren't
visible via plain `getattr` on the class itself, so `_find_node_class` would
find zero matches and raise `GraphBuilderError` (a loud, if initially
confusing, failure — not a silent no-op).
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

    A `ModuleNotFoundError` raised while importing `src.nodes.{kind}.{name}`
    itself (the module genuinely doesn't exist yet) is treated as "not
    implemented yet" and falls through to the next `kind`/`NoOpNode`. A
    `ModuleNotFoundError` raised because the module *does* exist but one of
    its own transitive imports is missing (a typo'd or genuinely missing
    dependency in a landed node module) is a real bug — it is re-raised as a
    `GraphBuilderError` rather than being silently swallowed into a no-op.
    The two are distinguished via `ModuleNotFoundError.name`: it equals the
    exact module path we asked for only in the "doesn't exist" case.
    """
    for kind in _NODE_KINDS:
        module_path = f"src.nodes.{kind}.{name}"
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as err:
            if err.name == module_path:
                continue
            raise GraphBuilderError(
                f"Node module '{module_path}' exists but failed to import "
                f"because one of its own imports is missing: {err}"
            ) from err
        cls = _find_node_class(module, name)
        return cls()
    return NoOpNode(name)
