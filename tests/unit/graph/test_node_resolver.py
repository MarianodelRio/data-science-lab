"""Unit tests for `src.graph.node_resolver.resolve_node`.

No real files are written under `src/nodes/` (that's outside this task's
`folders:` and would collide with future node tasks) — fake modules are
injected directly into `sys.modules` via `monkeypatch.setitem`, which is
reverted automatically at the end of each test.
"""

import importlib
import sys
import types

import pytest

from src.graph import node_resolver as node_resolver_module
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


def test_resolve_node_raises_on_broken_transitive_import_instead_of_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A node module that DOES exist but itself fails to import because one of
    its own dependencies is missing (e.g. a typo'd or genuinely-missing
    package) must surface as a `GraphBuilderError` — not be silently treated
    the same as "module not implemented yet" and fall back to `NoOpNode`.

    `node_resolver`'s own `importlib` name binding is monkeypatched to a
    stand-in (rather than injecting a fake module into `sys.modules`, which
    can't represent an import that fails partway through, or patching the
    real global `importlib` module, which would affect unrelated code during
    the test) to raise the exact `ModuleNotFoundError` Python raises for a
    broken transitive import: `.name` set to the *missing dependency*, not to
    the node module path itself.
    """
    module_path = "src.nodes.llm.broken_import_fake_node"
    real_import_module = importlib.import_module

    class _FakeImportlib:
        @staticmethod
        def import_module(dotted_path: str) -> types.ModuleType:
            if dotted_path == module_path:
                raise ModuleNotFoundError(
                    "No module named 'totally_nonexistent_dependency_xyz'",
                    name="totally_nonexistent_dependency_xyz",
                )
            return real_import_module(dotted_path)

    monkeypatch.setattr(node_resolver_module, "importlib", _FakeImportlib())

    with pytest.raises(GraphBuilderError):
        resolve_node("broken_import_fake_node")


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


def test_resolve_node_raises_when_name_is_a_pydantic_field_not_class_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A node class written as a Pydantic v2 `BaseModel` subclass with `name:
    str = "..."` declared as a typed field does NOT satisfy `_find_node_class`
    — Pydantic v2 doesn't expose field defaults via plain `getattr` on the
    class itself (only on instances, after `__init__`/validation). This
    locks in and documents the current, acceptable behavior: `resolve_node`
    fails loudly with `GraphBuilderError` (zero matches) rather than
    silently resolving nothing or crashing unhelpfully. See node_resolver's
    module docstring and docs/pipeline.md's node-module convention section.
    """
    from pydantic import BaseModel

    module_name = "src.nodes.compute.pydantic_field_name_fake_node"
    module = _inject_module(monkeypatch, module_name)

    class PydanticFieldNameFakeNode(BaseModel):
        name: str = "pydantic_field_name_fake_node"

        def __call__(self, state: dict) -> dict:
            return {}

    PydanticFieldNameFakeNode.__module__ = module_name
    module.PydanticFieldNameFakeNode = PydanticFieldNameFakeNode  # type: ignore[attr-defined]

    # Sanity-check the premise: plain getattr on the class does NOT see the
    # field default (it's only materialized on instances).
    assert getattr(PydanticFieldNameFakeNode, "name", None) != "pydantic_field_name_fake_node"

    with pytest.raises(GraphBuilderError):
        resolve_node("pydantic_field_name_fake_node")
