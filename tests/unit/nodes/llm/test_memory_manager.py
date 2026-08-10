"""Unit tests for src/nodes/llm/memory_manager.py.

All external calls are mocked: `LLMFactory`/the LLM itself, `WorkspaceManager`
(patched at both its `base.py` import location and its `_research_common.py`
import location, the latter used by the shared `read_problem_type` helper —
see T-019's decisions.md entry on the `_research_common` extraction), and
`RagStore` (via the in-memory `FakeRagStore` double defined below). No
network calls anywhere in this file.
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
from src.memory.store import IndexDocument
from src.nodes.llm.memory_manager import MemoryManagerNode
from src.state import new_state

DOC_A = IndexDocument(
    id="doc-a",
    text="Gradient boosting works well on tabular data with high-cardinality categoricals.",
    source="https://arxiv.org/abs/0000.00001",
    problem_type=["binary_classification"],
    methods_used=["gradient_boosting"],
    dataset_characteristics=["tabular"],
    key_findings="Gradient boosting is a strong baseline for tabular binary classification.",
    relevance_score=0.7,
)
DOC_B = IndexDocument(
    id="doc-b",
    text="Boosted trees are effective on tabular datasets with many categorical features.",
    source="https://www.semanticscholar.org/paper/abc123",
    problem_type=["binary_classification"],
    methods_used=["gradient_boosting", "xgboost"],
    dataset_characteristics=["tabular", "high_cardinality_categoricals"],
    key_findings="Boosted trees handle high-cardinality categoricals well.",
    relevance_score=0.6,
)
DOC_C = IndexDocument(
    id="doc-c",
    text="Time series forecasting with transformers shows promise for long horizons.",
    source="https://arxiv.org/abs/0000.00002",
    problem_type=["time_series_forecasting"],
    methods_used=["transformer"],
    dataset_characteristics=["time_series"],
    key_findings="Transformers extend forecasting horizons.",
    relevance_score=0.4,
)
PROBLEM_DEFINITION = {
    "problem_type": "binary_classification",
    "success_metric": "roc_auc",
    "constraints": [],
}

MERGED_CLUSTER_RESPONSE = [
    {
        "indices": [1, 2],
        "problem_type": ["binary_classification"],
        "methods_used": ["gradient_boosting", "xgboost"],
        "dataset_characteristics": ["tabular", "high_cardinality_categoricals"],
        "key_findings": "Gradient boosting / boosted trees are a strong tabular baseline.",
        "relevance_score": 0.85,
    }
]

SINGLETON_CLUSTERS_RESPONSE = [
    {
        "indices": [1],
        "problem_type": ["binary_classification"],
        "methods_used": ["gradient_boosting"],
        "dataset_characteristics": ["tabular"],
        "key_findings": "Gradient boosting is a strong baseline for tabular binary classification.",
        "relevance_score": 0.7,
    },
    {
        "indices": [2],
        "problem_type": ["time_series_forecasting"],
        "methods_used": ["transformer"],
        "dataset_characteristics": ["time_series"],
        "key_findings": "Transformers extend forecasting horizons.",
        "relevance_score": 0.4,
    },
]


class FakeRagStore:
    def __init__(self, documents: list[IndexDocument]) -> None:
        self._by_id = {doc.id: doc for doc in documents}
        self.index_calls: list[list[IndexDocument]] = []
        self.queries: list[str] = []

    def query(
        self, text: str, where: dict[str, Any] | None = None, n_results: int = 10
    ) -> list[IndexDocument]:
        self.queries.append(text)
        return list(self._by_id.values())[:n_results]

    def index(self, documents: list[IndexDocument]) -> None:
        self.index_calls.append(documents)
        for doc in documents:
            self._by_id[doc.id] = doc


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=json.dumps(MERGED_CLUSTER_RESPONSE))
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
    instance.write_text.return_value = "/workspace/reports/memory_consolidation.md"
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
    config = load_agent_config("memory_manager")

    assert config.name == "memory_manager"
    assert config.model_role == "fast"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "reports/memory_consolidation.md"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("memory_manager", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — memory_manager" in prompt


# -- zero-arg construction --


def test_zero_arg_construction_succeeds_with_no_rag_store(
    patched_llm_factory, patched_settings
) -> None:
    node = MemoryManagerNode()

    assert node._rag_store is None


# -- __call__ behavior --


def test_two_near_duplicate_candidates_consolidate_to_one(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    assert len(rag_store.index_calls) == 1
    (consolidated,) = rag_store.index_calls
    assert len(consolidated) == 1
    document = consolidated[0]
    assert document.id == "doc-a"
    assert document.methods_used == ["gradient_boosting", "xgboost"]


def test_query_returns_consolidated_set(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """A subsequent query returns the merged record's fields under the
    canonical id `doc-a`, proving consolidation happened.

    Note: because `RagStore.index()` only upserts (there is no `delete()`,
    per this task's approved query-window-consolidation scope), `doc-b`'s
    original row is never physically removed from the fake store — it is
    still present as a stale, superseded entry. `FakeRagStore` faithfully
    mirrors this real limitation (see the module docstring's caveat and
    `context/decisions.md`), so this test asserts on the canonical entry's
    content rather than on a literal total result count of 1.
    """
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    node(state)
    result = rag_store.query("anything")

    by_id = {doc.id: doc for doc in result}
    assert by_id["doc-a"].methods_used == ["gradient_boosting", "xgboost"]
    assert by_id["doc-a"].relevance_score == 0.85
    # doc-b's stale sibling row persists — documented caveat, not a bug.
    assert set(by_id) == {"doc-a", "doc-b"}


def test_no_duplicates_found_reindexes_each_candidate(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(SINGLETON_CLUSTERS_RESPONSE))
    rag_store = FakeRagStore([DOC_A, DOC_C])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    (consolidated,) = rag_store.index_calls
    assert len(consolidated) == 2
    assert {doc.id for doc in consolidated} == {"doc-a", "doc-c"}
    result = rag_store.query("anything")
    assert len(result) == 2


def test_zero_candidates_indexes_empty_list_without_raising(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content="[]")
    rag_store = FakeRagStore([])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    assert rag_store.index_calls == [[]]
    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_text.call_args
    assert "No documents were retrieved for this query." in args[1]


def test_cluster_indices_not_partition_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    bad_clusters = [{**MERGED_CLUSTER_RESPONSE[0], "indices": [1]}]  # missing index 2 -> gap
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(bad_clusters))
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="memory_manager"):
        node(state)


def test_malformed_json_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content="not json at all {")
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="memory_manager"):
        node(state)


# -- validator negative paths (mirrors test_competition_analyst.py's
# post-T-018 coverage for the equivalent `_research_common` validators —
# context/decisions.md's T-018 entry documents an unbounded relevance_score
# shipping untested once before, caught only by review) --


def test_relevance_score_above_one_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    bad_clusters = [{**MERGED_CLUSTER_RESPONSE[0], "relevance_score": 1.5}]
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(bad_clusters))
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="relevance_score"):
        node(state)

    assert rag_store.index_calls == []


def test_relevance_score_negative_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    bad_clusters = [{**MERGED_CLUSTER_RESPONSE[0], "relevance_score": -0.1}]
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(bad_clusters))
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="relevance_score"):
        node(state)

    assert rag_store.index_calls == []


def test_relevance_score_bool_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    # `isinstance(True, int)` is `True` in Python — the validator must reject
    # bools explicitly rather than silently accepting `True`/`False` as 1.0/0.0.
    bad_clusters = [{**MERGED_CLUSTER_RESPONSE[0], "relevance_score": True}]
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(bad_clusters))
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="relevance_score"):
        node(state)

    assert rag_store.index_calls == []


def test_relevance_score_non_numeric_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    bad_clusters = [{**MERGED_CLUSTER_RESPONSE[0], "relevance_score": "high"}]
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(bad_clusters))
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="relevance_score"):
        node(state)

    assert rag_store.index_calls == []


def test_non_string_list_item_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    bad_clusters = [{**MERGED_CLUSTER_RESPONSE[0], "methods_used": ["xgboost", 123]}]
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(bad_clusters))
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="methods_used"):
        node(state)

    assert rag_store.index_calls == []


def test_cluster_entry_not_a_dict_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=json.dumps([1, 2]))
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="memory_manager"):
        node(state)

    assert rag_store.index_calls == []


def test_cluster_indices_empty_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    bad_clusters = [{**MERGED_CLUSTER_RESPONSE[0], "indices": []}]
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(bad_clusters))
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="indices"):
        node(state)

    assert rag_store.index_calls == []


def test_cluster_indices_with_duplicate_within_cluster_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    bad_clusters = [{**MERGED_CLUSTER_RESPONSE[0], "indices": [1, 1]}]
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(bad_clusters))
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    with pytest.raises(ValueError, match="duplicates"):
        node(state)

    assert rag_store.index_calls == []


def test_canonical_id_is_lowest_original_index_regardless_of_llm_order(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm: MagicMock
) -> None:
    """`indices: [2, 1]` (unsorted, as the LLM might return them) must still
    pick candidate 1's id (`doc-a`) as canonical, not whichever index the LLM
    happened to list first — `_build_consolidated_documents` sorts `indices`
    before selecting `members[0]`."""
    unsorted_cluster = [{**MERGED_CLUSTER_RESPONSE[0], "indices": [2, 1]}]
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(unsorted_cluster))
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    (consolidated,) = rag_store.index_calls
    assert consolidated[0].id == "doc-a"


def test_call_state_delta_is_messages_only(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    """Regression guard: memory_manager owns no LabState field of its own,
    so its delta must never grow beyond `messages`."""
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    delta = node(state)

    assert set(delta.keys()) == {"messages"}


# -- query-building (same pattern as literature_researcher's own tests) --


def test_query_built_from_problem_type_when_readable(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    assert rag_store.queries == ["binary_classification machine learning techniques for comp"]


def test_query_falls_back_when_problem_definition_path_empty(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()
    state["problem_definition_path"] = ""

    node(state)

    assert rag_store.queries == ["machine learning techniques for comp"]


def test_query_falls_back_when_problem_definition_unreadable(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("not found")
    rag_store = FakeRagStore([DOC_A, DOC_B])
    node = MemoryManagerNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    assert rag_store.queries == ["machine learning techniques for comp"]
