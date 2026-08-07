"""Unit tests for src/nodes/llm/competition_analyst.py.

All external calls are mocked: `LLMFactory`/the LLM itself and `WorkspaceManager`
(patched at their import location inside `src.nodes.llm.base`, matching
`test_validation_strategist.py`'s convention). `kernel_lister` and `rag_store` are
always injected fakes/mocks — no real `kaggle_client` or Chroma call happens, and
no real network call is made anywhere in this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings, WorkspaceConfig
from src.nodes.llm.competition_analyst import CompetitionAnalystNode
from src.state import new_state

KERNELS = [
    {
        "ref": "someone/lightgbm-baseline",
        "title": "LightGBM baseline — 0.91 CV",
        "author": "someone",
        "total_votes": 120,
        "url": "https://www.kaggle.com/code/someone/lightgbm-baseline",
    },
    {
        "ref": "other/my-notebook",
        "title": "My Notebook",
        "author": "other",
        "total_votes": 5,
        "url": "https://www.kaggle.com/code/other/my-notebook",
    },
]

EXTRACTIONS = [
    {
        "index": 1,
        "problem_type": ["binary_classification"],
        "methods_used": ["LightGBM", "target encoding"],
        "dataset_characteristics": ["imbalanced target"],
        "key_findings": "Gradient boosting baseline scores well with minimal tuning.",
        "relevance_score": 0.9,
    },
    {
        "index": 2,
        "problem_type": [],
        "methods_used": [],
        "dataset_characteristics": [],
        "key_findings": "",
        "relevance_score": 0.1,
    },
]

RESPONSE_CONTENT = json.dumps(EXTRACTIONS)


def _fake_kernel_lister(expected_competition: str, expected_n: int, kernels: list[dict[str, Any]]):
    calls: list[tuple[str, int]] = []

    def _lister(competition: str, n: int = 10) -> list[dict[str, Any]]:
        calls.append((competition, n))
        return kernels

    _lister.calls = calls  # type: ignore[attr-defined]
    return _lister


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    settings.workspace = WorkspaceConfig(
        root="/competitions",
        chroma_host="chroma",
        chroma_port=8000,
        mlflow_tracking_uri="http://mlflow:5000",
    )
    return settings


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=RESPONSE_CONTENT)
    return llm


@pytest.fixture
def patched_llm_factory(mock_llm: MagicMock):
    with patch("src.nodes.llm.base.LLMFactory") as mock_factory:
        mock_factory.get.return_value = mock_llm
        yield mock_factory


@pytest.fixture
def patched_settings():
    with patch("src.nodes.llm.base.Settings") as mock_settings_cls:
        mock_settings_cls.load.return_value = _make_settings()
        yield mock_settings_cls


@pytest.fixture
def mock_workspace_manager():
    with patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls:
        instance = MagicMock()
        instance.workspace_path = Path("/workspace")
        instance.write_text.return_value = "/workspace/reports/competition_analysis_iter0.md"
        mock_wm_cls.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("titanic", "/workspace")
    state["current_iteration"] = current_iteration
    return state


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("competition_analyst")

    assert config.name == "competition_analyst"
    assert config.model_role == "research"
    assert config.prompt_version == "v1"
    assert config.tools == ("kaggle_client",)
    assert config.output_file_pattern == "reports/competition_analysis_iter{iteration}.md"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("competition_analyst", "v1")
    assert prompt.strip() != ""
    assert "## Kernels" in prompt


# -- construction --


def test_node_constructs_with_no_args(patched_llm_factory, patched_settings) -> None:
    node = CompetitionAnalystNode()

    assert node.name == "competition_analyst"
    # The default kernel_lister is the bare `list_top_kernels` function
    # reference — never called at construction time, so no credential/network
    # error can be raised here even without KAGGLE_USERNAME/KAGGLE_KEY set.
    from src.tools.kaggle_client import list_top_kernels

    assert node._kernel_lister is list_top_kernels


# -- __call__ behavior --


def test_call_indexes_documents_with_nonempty_methods_used(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_rag_store = MagicMock()
    lister = _fake_kernel_lister("titanic", 10, KERNELS)
    node = CompetitionAnalystNode(kernel_lister=lister, rag_store=mock_rag_store)
    state = _build_state()

    node(state)

    mock_rag_store.index.assert_called_once()
    (documents,), _ = mock_rag_store.index.call_args
    assert len(documents) >= 1
    assert any(doc.methods_used for doc in documents)
    assert documents[0].methods_used == ["LightGBM", "target encoding"]


def test_report_written_with_kernel_title_and_findings(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    mock_rag_store = MagicMock()
    lister = _fake_kernel_lister("titanic", 10, KERNELS)
    node = CompetitionAnalystNode(kernel_lister=lister, rag_store=mock_rag_store)
    state = _build_state()

    node(state)

    workspace_instance.write_text.assert_called_once()
    args, _ = workspace_instance.write_text.call_args
    assert args[0] == "reports/competition_analysis_iter0.md"
    report = args[1]
    assert "LightGBM baseline" in report or "someone/lightgbm-baseline" in report
    assert "Gradient boosting baseline scores well with minimal tuning." in report


def test_call_delta_only_touches_messages(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_rag_store = MagicMock()
    lister = _fake_kernel_lister("titanic", 10, KERNELS)
    node = CompetitionAnalystNode(kernel_lister=lister, rag_store=mock_rag_store)
    state = _build_state()

    delta = node(state)

    assert set(delta.keys()) == {"messages"}


def test_kernel_lister_called_with_competition_name_and_top_n(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_rag_store = MagicMock()
    lister = _fake_kernel_lister("titanic", 7, KERNELS)
    node = CompetitionAnalystNode(kernel_lister=lister, rag_store=mock_rag_store, top_n=7)
    state = _build_state()

    node(state)

    assert lister.calls == [("titanic", 7)]


def test_empty_kernel_list_indexes_empty_list_without_raising(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="[]")
    mock_rag_store = MagicMock()
    lister = _fake_kernel_lister("titanic", 10, [])
    node = CompetitionAnalystNode(kernel_lister=lister, rag_store=mock_rag_store)
    state = _build_state()

    node(state)

    mock_rag_store.index.assert_called_once_with([])


def test_out_of_range_extraction_index_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    bad_extractions = [{**EXTRACTIONS[0], "index": 99}]
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(bad_extractions))
    mock_rag_store = MagicMock()
    lister = _fake_kernel_lister("titanic", 10, KERNELS)
    node = CompetitionAnalystNode(kernel_lister=lister, rag_store=mock_rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="index"):
        node(state)

    mock_rag_store.index.assert_not_called()


def test_malformed_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="not json at all")
    mock_rag_store = MagicMock()
    lister = _fake_kernel_lister("titanic", 10, KERNELS)
    node = CompetitionAnalystNode(kernel_lister=lister, rag_store=mock_rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="not valid JSON"):
        node(state)

    mock_rag_store.index.assert_not_called()


def test_default_rag_store_constructed_lazily_when_not_injected(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    lister = _fake_kernel_lister("titanic", 10, KERNELS)
    node = CompetitionAnalystNode(kernel_lister=lister)
    state = _build_state()

    with (
        patch("src.nodes.llm.competition_analyst.RagStore") as mock_rag_store_cls,
        patch("src.nodes.llm.competition_analyst.Settings") as mock_settings_cls,
    ):
        mock_rag_store_cls.return_value = MagicMock()
        mock_settings_cls.load.return_value = _make_settings()
        node(state)

        mock_rag_store_cls.assert_called_once()
        args, kwargs = mock_rag_store_cls.call_args
        assert args[0] == "titanic" or kwargs.get("competition_name") == "titanic"
