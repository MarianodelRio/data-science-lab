"""Unit tests for `src.graph.node_resolver.resolve_node`.

No real files are written under `src/nodes/` (that's outside this task's
`folders:` and would collide with future node tasks) — fake modules are
injected directly into `sys.modules` via `monkeypatch.setitem`, which is
reverted automatically at the end of each test.
"""

import sys
import types

import pytest

from src.graph.errors import GraphBuilderError
from src.graph.node_resolver import resolve_node
from src.graph.nodes_noop import NoOpNode


def _inject_module(monkeypatch: pytest.MonkeyPatch, module_name: str) -> types.ModuleType:
    module = types.ModuleType(module_name)
    monkeypatch.setitem(sys.modules, module_name, module)
    return module


def test_resolve_node_returns_matching_class_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    module_name = "src.nodes.compute.fake_node_for_test"
    module = _inject_module(monkeypatch, module_name)

    class FakeNode:
        name = "fake_node_for_test"

        def __call__(self, state: dict) -> dict:
            return {}

    FakeNode.__module__ = module_name
    module.FakeNode = FakeNode  # type: ignore[attr-defined]

    resolved = resolve_node("fake_node_for_test")

    assert isinstance(resolved, FakeNode)


def test_resolve_node_prefers_llm_over_compute(monkeypatch: pytest.MonkeyPatch) -> None:
    llm_module_name = "src.nodes.llm.dual_kind_fake_node"
    compute_module_name = "src.nodes.compute.dual_kind_fake_node"
    llm_module = _inject_module(monkeypatch, llm_module_name)
    _inject_module(monkeypatch, compute_module_name)

    class LlmFakeNode:
        name = "dual_kind_fake_node"

        def __call__(self, state: dict) -> dict:
            return {}

    LlmFakeNode.__module__ = llm_module_name
    llm_module.LlmFakeNode = LlmFakeNode  # type: ignore[attr-defined]
    # compute_module intentionally has no matching class — llm must win first anyway.

    resolved = resolve_node("dual_kind_fake_node")

    assert isinstance(resolved, LlmFakeNode)


def test_resolve_node_falls_back_to_noop_when_module_missing_for_both_kinds() -> None:
    resolved = resolve_node("definitely_not_a_real_node_xyz123")

    assert isinstance(resolved, NoOpNode)
    assert resolved.name == "definitely_not_a_real_node_xyz123"


def test_noop_fallback_never_raises_and_returns_empty_dict() -> None:
    resolved = resolve_node("also_not_a_real_node_abc456")

    assert resolved({"phase": "phase1_understanding"}) == {}  # type: ignore[arg-type]


def test_resolve_node_raises_when_module_has_no_matching_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "src.nodes.compute.empty_fake_node"
    _inject_module(monkeypatch, module_name)  # module exists, defines nothing

    with pytest.raises(GraphBuilderError):
        resolve_node("empty_fake_node")


def test_resolve_node_raises_when_module_has_multiple_matching_classes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "src.nodes.compute.ambiguous_fake_node"
    module = _inject_module(monkeypatch, module_name)

    class FirstFakeNode:
        name = "ambiguous_fake_node"

        def __call__(self, state: dict) -> dict:
            return {}

    class SecondFakeNode:
        name = "ambiguous_fake_node"

        def __call__(self, state: dict) -> dict:
            return {}

    FirstFakeNode.__module__ = module_name
    SecondFakeNode.__module__ = module_name
    module.FirstFakeNode = FirstFakeNode  # type: ignore[attr-defined]
    module.SecondFakeNode = SecondFakeNode  # type: ignore[attr-defined]

    with pytest.raises(GraphBuilderError):
        resolve_node("ambiguous_fake_node")
