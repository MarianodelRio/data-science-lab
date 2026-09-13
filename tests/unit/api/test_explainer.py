"""Unit tests for `src/api/explainer.py`.

`LLMFactory` is mocked at its import location inside `src.api.explainer` in
every test that constructs an `Explainer` — no real network calls. `workspace`
and `rag_store` are plain `MagicMock()`s, matching the codebase's existing
node-test style (see `tests/unit/nodes/llm/test_base.py`).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.api.explainer import Explainer
from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content="the answer")
    return llm


@pytest.fixture
def patched_llm_factory(mock_llm: MagicMock):
    with patch("src.api.explainer.LLMFactory") as mock_factory:
        mock_factory.get.return_value = mock_llm
        yield mock_factory


@pytest.fixture
def workspace() -> MagicMock:
    ws = MagicMock()
    ws.workspace_path = Path("/workspace")
    return ws


@pytest.fixture
def explainer(patched_llm_factory: MagicMock, workspace: MagicMock) -> Explainer:
    return Explainer(workspace=workspace, state_reader=lambda: {})


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("explainer")
    assert config.model_role == "reasoning"
    assert config.output_file_pattern == ""
    assert config.prompt_version == "v1"

    prompt_text = PromptLoader().load("explainer", "v1")
    assert prompt_text.strip() != ""


def test_answer_returns_llm_response_text(explainer: Explainer, mock_llm: MagicMock) -> None:
    result = explainer.answer("what phase is this run in?")

    assert result == "the answer"
    mock_llm.invoke.assert_called_once()


def test_answer_reads_workspace_file_via_workspace_manager(
    patched_llm_factory: MagicMock, mock_llm: MagicMock, workspace: MagicMock
) -> None:
    workspace.read_text.return_value = "EDA findings go here"
    state_reader = MagicMock(return_value={"eda_report_path": "/workspace/reports/eda_report.md"})
    explainer = Explainer(workspace=workspace, state_reader=state_reader)

    explainer.answer("what did the EDA find?")

    workspace.read_text.assert_called_once_with("reports/eda_report.md")
    messages = mock_llm.invoke.call_args.args[0]
    assert any("EDA findings go here" in _content(m) for m in messages)


def test_answer_never_writes_to_workspace(
    patched_llm_factory: MagicMock, workspace: MagicMock
) -> None:
    state_reader = MagicMock(
        return_value={
            "eda_report_path": "/workspace/reports/eda_report.md",
            "problem_definition_path": "/workspace/reports/problem_definition.json",
        }
    )
    workspace.read_text.return_value = "text"
    workspace.read_json.return_value = {"problem_type": "regression"}
    explainer = Explainer(workspace=workspace, state_reader=state_reader)

    explainer.answer("anything?")

    workspace.write_text.assert_not_called()
    workspace.write_json.assert_not_called()
    workspace.write_notebook.assert_not_called()
    workspace.ensure_dir.assert_not_called()


def test_answer_handles_missing_workspace_file_gracefully(
    patched_llm_factory: MagicMock, mock_llm: MagicMock, workspace: MagicMock
) -> None:
    workspace.read_text.side_effect = FileNotFoundError
    state_reader = MagicMock(return_value={"eda_report_path": "/workspace/reports/eda_report.md"})
    explainer = Explainer(workspace=workspace, state_reader=state_reader)

    result = explainer.answer("what did the EDA find?")

    assert result == "the answer"


def test_answer_includes_rag_hits_when_available(
    patched_llm_factory: MagicMock, mock_llm: MagicMock, workspace: MagicMock
) -> None:
    hit = MagicMock()
    hit.key_findings = "gradient boosting worked well on similar tabular data"
    rag_store = MagicMock()
    rag_store.query.return_value = [hit]
    explainer = Explainer(workspace=workspace, state_reader=lambda: {}, rag_store=rag_store)

    explainer.answer("what approach should we try?")

    messages = mock_llm.invoke.call_args.args[0]
    assert any("gradient boosting worked well" in _content(m) for m in messages)


def test_answer_swallows_rag_store_failure(
    patched_llm_factory: MagicMock, mock_llm: MagicMock, workspace: MagicMock
) -> None:
    rag_store = MagicMock()
    rag_store.query.side_effect = RuntimeError("boom")
    explainer = Explainer(workspace=workspace, state_reader=lambda: {}, rag_store=rag_store)

    result = explainer.answer("what approach should we try?")

    assert result == "the answer"


def test_answer_includes_conversation_history(explainer: Explainer, mock_llm: MagicMock) -> None:
    history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]

    explainer.answer("final question", history=history)

    messages = mock_llm.invoke.call_args.args[0]
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert messages[1].content == "a"
    assert isinstance(messages[2], AIMessage)
    assert messages[2].content == "b"
    assert isinstance(messages[3], HumanMessage)
    assert messages[3].content == "final question"


def _content(message) -> str:
    return message.content if isinstance(message.content, str) else str(message.content)
