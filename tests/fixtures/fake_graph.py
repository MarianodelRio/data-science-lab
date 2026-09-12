"""Fake `CompiledStateGraph` stand-in for `src/api/` unit tests.

Implements the same three methods the API layer actually calls
(`invoke`, `get_state`, `update_state`) and records every call so tests can
assert on exactly what the router passed through, without importing
`langgraph`/`sqlite3`/`GraphBuilder` at all.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class FakeSnapshot:
    """Stand-in for the LangGraph `StateSnapshot` returned by `get_state`."""

    values: dict[str, Any]
    next: tuple[str, ...] = ()


class FakeCompiledGraph:
    """Records every `invoke`/`get_state`/`update_state` call it receives."""

    def __init__(self, values: dict | None = None, next_nodes: tuple[str, ...] = ()) -> None:
        self.invoke_calls: list[tuple[Any, dict]] = []
        self.update_state_calls: list[tuple[dict, dict]] = []
        self._values: dict[str, Any] = values or {}
        self._next = next_nodes

    def invoke(self, input: Any, config: dict) -> Any:
        self.invoke_calls.append((input, config))
        return self._values

    def get_state(self, config: dict) -> FakeSnapshot:
        return FakeSnapshot(values=self._values, next=self._next)

    def update_state(self, config: dict, values: dict) -> None:
        self.update_state_calls.append((config, values))
        self._values.update(values)
