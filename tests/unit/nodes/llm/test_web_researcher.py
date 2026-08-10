"""Unit tests for src/nodes/llm/web_researcher.py.

All external calls are mocked: `LLMFactory`/the LLM itself, `WorkspaceManager`
(patched at both its `base.py` import location and its `_research_common.py`
import location, the latter used by the shared `read_problem_type` helper —
see T-019's decisions.md entry on the `_research_common` extraction), the
injected search client, and `RagStore`. `urllib.request.urlopen` is
monkeypatched (never a real socket) for the default `WebSearchClient` tests.
No network calls anywhere in this file.
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
from src.config.settings import ContextConfig, Settings
from src.nodes.llm._research_common import SourceDocument
from src.nodes.llm.web_researcher import WebResearcherNode, WebSearchClient
from src.state import new_state

FAKE_SOURCE = SourceDocument(
    title="Kaggle Write-up: Winning with Stacking",
    text="A blog post describing a stacking ensemble that won a tabular competition.",
    url="https://example.com/blog/winning-with-stacking",
)
VALID_EXTRACTION = [
    {
        "index": 1,
        "problem_type": ["regression"],
        "methods_used": ["stacking"],
        "dataset_characteristics": ["tabular"],
        "key_findings": "Stacking multiple models improved the leaderboard score.",
        "relevance_score": 0.7,
    }
]
PROBLEM_DEFINITION = {
    "problem_type": "regression",
    "success_metric": "rmse",
    "constraints": [],
}


class FakeSearchClient:
    def __init__(self, sources: list[SourceDocument]) -> None:
        self.sources = sources
        self.queries: list[str] = []

    def search(self, query: str) -> list[SourceDocument]:
        self.queries.append(query)
        return self.sources


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=json.dumps(VALID_EXTRACTION))
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
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_json.return_value = PROBLEM_DEFINITION
    instance.write_text.return_value = "/workspace/reports/web_research.md"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm._research_common.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    state["problem_definition_path"] = "reports/problem_definition.json"
    return state


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("web_researcher")

    assert config.name == "web_researcher"
    assert config.model_role == "research"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "reports/web_research.md"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("web_researcher", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — web_researcher" in prompt


# -- zero-arg construction --


def test_zero_arg_construction_succeeds_with_no_client_or_rag_store(
    patched_llm_factory, patched_settings
) -> None:
    node = WebResearcherNode()

    assert node._client is None
    assert node._rag_store is None


# -- __call__ behavior --


def test_call_indexes_document_with_populated_metadata(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    fake_client = FakeSearchClient([FAKE_SOURCE])
    rag_store = MagicMock()
    node = WebResearcherNode(client=fake_client, rag_store=rag_store)
    state = _build_state()

    node(state)

    rag_store.index.assert_called_once()
    (documents,), _ = rag_store.index.call_args
    assert len(documents) == 1
    document = documents[0]
    assert document.source == FAKE_SOURCE.url
    assert document.problem_type == ["regression"]
    assert document.methods_used == ["stacking"]
    assert document.relevance_score == 0.7


def test_call_writes_report_to_web_research_output_path(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    fake_client = FakeSearchClient([FAKE_SOURCE])
    node = WebResearcherNode(client=fake_client, rag_store=MagicMock())
    state = _build_state()

    node(state)

    workspace_instance.write_text.assert_called_once()
    args, _ = workspace_instance.write_text.call_args
    assert args[0] == "reports/web_research.md"
    assert FAKE_SOURCE.title in args[1]
    assert VALID_EXTRACTION[0]["key_findings"] in args[1]


def test_call_state_delta_is_messages_only(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    fake_client = FakeSearchClient([FAKE_SOURCE])
    node = WebResearcherNode(client=fake_client, rag_store=MagicMock())
    state = _build_state()

    delta = node(state)

    assert set(delta.keys()) == {"messages"}


def test_query_built_from_problem_type_when_readable(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    fake_client = FakeSearchClient([FAKE_SOURCE])
    node = WebResearcherNode(client=fake_client, rag_store=MagicMock())
    state = _build_state()

    node(state)

    assert fake_client.queries == ["regression machine learning techniques for comp"]


def test_query_falls_back_when_problem_definition_path_empty(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    fake_client = FakeSearchClient([FAKE_SOURCE])
    node = WebResearcherNode(client=fake_client, rag_store=MagicMock())
    state = _build_state()
    state["problem_definition_path"] = ""

    node(state)

    assert fake_client.queries == ["machine learning techniques for comp"]


def test_query_falls_back_when_problem_definition_unreadable(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("not found")
    fake_client = FakeSearchClient([FAKE_SOURCE])
    node = WebResearcherNode(client=fake_client, rag_store=MagicMock())
    state = _build_state()

    node(state)

    assert fake_client.queries == ["machine learning techniques for comp"]


def test_no_sources_found_indexes_empty_list_without_raising(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content="[]")
    fake_client = FakeSearchClient([])
    rag_store = MagicMock()
    node = WebResearcherNode(client=fake_client, rag_store=rag_store)
    state = _build_state()

    node(state)

    rag_store.index.assert_called_once_with([])


def test_malformed_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    fake_client = FakeSearchClient([FAKE_SOURCE])
    node = WebResearcherNode(client=fake_client, rag_store=MagicMock())
    state = _build_state()

    with pytest.raises(ValueError, match="web_researcher"):
        node(state)


# -- default WebSearchClient (urlopen monkeypatched, no network) --


def test_web_search_client_missing_api_key_raises_before_any_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    mock_urlopen = MagicMock()
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        WebSearchClient().search("stacking ensembles")

    mock_urlopen.assert_not_called()


def test_web_search_client_parses_tavily_results_with_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    tavily_payload = json.dumps(
        {
            "results": [
                {
                    "title": "Stacking for Tabular Data",
                    "content": "A discussion of stacking ensembles.",
                    "url": "https://example.com/stacking",
                }
            ]
        }
    ).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = tavily_payload
    mock_urlopen = MagicMock(return_value=cm)
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)

    sources = WebSearchClient().search("stacking ensembles")

    mock_urlopen.assert_called_once()
    request = mock_urlopen.call_args[0][0]
    assert request.get_method() == "POST"
    assert sources == [
        SourceDocument(
            title="Stacking for Tabular Data",
            text="A discussion of stacking ensembles.",
            url="https://example.com/stacking",
        )
    ]
