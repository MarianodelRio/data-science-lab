"""Unit tests for src/nodes/llm/base.py.

`LLMFactory.get` and `WorkspaceManager` are mocked at their import location
inside `src.nodes.llm.base` in every test that constructs a node — no real
network calls, no real filesystem writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.config.settings import ContextConfig, Settings
from src.nodes.llm.base import LLMNode, trim_context
from src.state import new_state

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"
AGENT_CONFIG_DIR = FIXTURES_DIR / "config" / "agents"
PROMPTS_DIR = FIXTURES_DIR / "prompts"


class DummyNode(LLMNode):
    name = "dummy"


class UnnamedNode(LLMNode):
    """Deliberately does not override `name` — must raise on construction."""


class MissingIterationNode(LLMNode):
    """Points at a fixture whose `output_file_pattern` has no `{iteration}`
    placeholder — must raise on construction (T-010 review finding 1)."""

    name = "missing_iteration"


class ExtraPlaceholderNode(LLMNode):
    """Points at a fixture whose `output_file_pattern` has a placeholder
    besides `{iteration}` (`{fold}`) — construction succeeds, but resolving
    the output path at call time must raise a clear `ValueError`, not a bare
    `KeyError` (T-010 review finding 2)."""

    name = "extra_placeholder"


def _make_settings(max_messages_per_node: int) -> MagicMock:
    """A MagicMock standing in for a real `Settings` instance, exposing only
    the `.context.max_messages_per_node` path `LLMNode.__init__` reads."""
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content="the response")
    return llm


@pytest.fixture
def patched_llm_factory(mock_llm: MagicMock):
    with patch("src.nodes.llm.base.LLMFactory") as mock_factory:
        mock_factory.get.return_value = mock_llm
        yield mock_factory


@pytest.fixture
def patched_settings():
    with patch("src.nodes.llm.base.Settings") as mock_settings_cls:
        mock_settings_cls.load.return_value = _make_settings(max_messages_per_node=10)
        yield mock_settings_cls


@pytest.fixture
def mock_workspace_manager():
    with patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls:
        instance = MagicMock()
        instance.write_text.return_value = "/workspace/dummy/iteration_0/output.txt"
        mock_wm_cls.return_value = instance
        yield mock_wm_cls, instance


def _build_state(messages: list[Any] | None = None, current_iteration: int = 0) -> dict:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    if messages is not None:
        state["messages"] = messages
    return state


# -- construction --


def test_init_loads_agent_config_and_prompt(patched_llm_factory, patched_settings) -> None:
    node = DummyNode(agent_config_dir=AGENT_CONFIG_DIR, prompts_dir=PROMPTS_DIR)

    assert node.config.name == "dummy"
    assert node.config.model_role == "fast"
    assert "Dummy prompt fixture" in node.system_prompt
    patched_llm_factory.get.assert_called_once_with("fast")


def test_init_requires_nonempty_name(patched_llm_factory, patched_settings) -> None:
    with pytest.raises(ValueError, match="non-empty class-level 'name'"):
        UnnamedNode(agent_config_dir=AGENT_CONFIG_DIR, prompts_dir=PROMPTS_DIR)


def test_init_raises_when_output_file_pattern_missing_iteration_placeholder(
    patched_llm_factory, patched_settings
) -> None:
    """Regression test for review finding 1: a pattern with no `{iteration}`
    placeholder must fail loudly at construction, not silently overwrite the
    same output file on every iteration."""
    with pytest.raises(ValueError, match=r"missing_iteration") as excinfo:
        MissingIterationNode(agent_config_dir=AGENT_CONFIG_DIR, prompts_dir=PROMPTS_DIR)

    assert "static/output.txt" in str(excinfo.value)
    assert "{iteration}" in str(excinfo.value)
    # Must fail before ever touching the LLM.
    patched_llm_factory.get.assert_not_called()


# -- __call__ --


def test_call_returns_state_delta_with_new_messages_only(
    patched_llm_factory, patched_settings, mock_llm: MagicMock, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = DummyNode(agent_config_dir=AGENT_CONFIG_DIR, prompts_dir=PROMPTS_DIR)
    state = _build_state(messages=[HumanMessage(content="prior message")])

    delta = node(state)

    assert delta["messages"] == [mock_llm.invoke.return_value]
    assert len(delta["messages"]) == 1


def test_call_writes_via_workspace_manager_not_directly(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_wm_cls, workspace_instance = mock_workspace_manager
    node = DummyNode(agent_config_dir=AGENT_CONFIG_DIR, prompts_dir=PROMPTS_DIR)
    state = _build_state(messages=[], current_iteration=0)

    node(state)

    mock_wm_cls.assert_called_once_with(state["workspace_path"])
    workspace_instance.write_text.assert_called_once_with(
        "dummy/iteration_0/output.txt", "the response"
    )

    base_source = Path(__file__).resolve().parents[4] / "src" / "nodes" / "llm" / "base.py"
    source = base_source.read_text(encoding="utf-8")
    assert "open(" not in source
    write_text_calls = [line for line in source.splitlines() if ".write_text(" in line]
    assert write_text_calls and all("workspace.write_text(" in line for line in write_text_calls)


def test_call_never_calls_llm_directly_over_network(
    patched_llm_factory, patched_settings, mock_llm: MagicMock, mock_workspace_manager
) -> None:
    node = DummyNode(agent_config_dir=AGENT_CONFIG_DIR, prompts_dir=PROMPTS_DIR)
    state = _build_state(messages=[])

    node(state)

    mock_llm.invoke.assert_called_once()


def test_context_trimming_keeps_only_last_n_messages(
    patched_llm_factory, mock_llm: MagicMock, mock_workspace_manager
) -> None:
    with patch("src.nodes.llm.base.Settings") as mock_settings_cls:
        mock_settings_cls.load.return_value = _make_settings(max_messages_per_node=2)
        node = DummyNode(agent_config_dir=AGENT_CONFIG_DIR, prompts_dir=PROMPTS_DIR)

    messages = [HumanMessage(content=f"msg {i}") for i in range(5)]
    state = _build_state(messages=messages)

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    assert len(invoked_messages) == 3  # system prompt + last 2 messages
    assert isinstance(invoked_messages[0], SystemMessage)
    assert invoked_messages[1] == messages[3]
    assert invoked_messages[2] == messages[4]


@pytest.mark.parametrize(
    "n, expected_len, expected_tail",
    [
        (0, 0, []),
        (-1, 0, []),
        (100, 5, [0, 1, 2, 3, 4]),
        (2, 2, [3, 4]),
    ],
)
def test_trim_context_standalone(n: int, expected_len: int, expected_tail: list[int]) -> None:
    messages = [HumanMessage(content=str(i)) for i in range(5)]

    result = trim_context(messages, n)

    assert len(result) == expected_len
    assert [int(m.content) for m in result] == expected_tail


def test_output_path_uses_current_iteration(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = DummyNode(agent_config_dir=AGENT_CONFIG_DIR, prompts_dir=PROMPTS_DIR)
    state = _build_state(messages=[], current_iteration=3)

    node(state)

    workspace_instance.write_text.assert_called_once_with(
        "dummy/iteration_3/output.txt", "the response"
    )


def test_call_raises_valueerror_on_unresolved_placeholder(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """Regression test for review finding 2: a pattern with an extra/typo'd
    placeholder besides `{iteration}` must surface as a clear `ValueError`
    naming the agent/pattern/missing key, not a bare `KeyError`."""
    node = ExtraPlaceholderNode(agent_config_dir=AGENT_CONFIG_DIR, prompts_dir=PROMPTS_DIR)
    state = _build_state(messages=[])

    with pytest.raises(ValueError, match=r"extra_placeholder") as excinfo:
        node(state)

    assert not isinstance(excinfo.value, KeyError)
    assert "fold" in str(excinfo.value)
    assert "cv_results/fold_{fold}_iter_{iteration}.json" in str(excinfo.value)


def test_default_build_output_state_returns_empty_dict(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    node = DummyNode(agent_config_dir=AGENT_CONFIG_DIR, prompts_dir=PROMPTS_DIR)
    state = _build_state(messages=[])

    delta = node(state)

    assert set(delta.keys()) == {"messages"}
