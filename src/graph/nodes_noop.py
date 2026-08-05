"""Placeholder node used by `resolve_node` when a real `src/nodes/{llm|compute}/{name}.py`
module does not exist yet.

Keeping this fallback inside `src/graph/` (rather than as stub files under
`src/nodes/`) means `GraphBuilder.build()` compiles a full 7-phase graph today,
before any node task (T-010+) has landed a single real node — and no future
node task collides with a stub file this task would otherwise own.
"""

from src.state import LabState


class NoOpNode:
    """A node that does nothing: never raises, never touches `LabState` beyond
    (at most) `messages`, and always returns an empty update dict.

    `name` matches the module-filename-stem convention used by real nodes so
    `resolve_node`'s fallback is indistinguishable in shape from a landed node.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, state: LabState) -> dict:
        return {}
