"""Unit tests for src/nodes/llm/hypothesis_generator.py.

All external calls are mocked: `LLMFactory`/the LLM itself, `WorkspaceManager`
(patched at both its `base.py` and its `hypothesis_generator.py` import
locations) and the `RagStore` (injected directly through the constructor,
matching `test_solution_architect.py`'s convention). No network calls, no real
filesystem writes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings
from src.nodes.llm.hypothesis_generator import HypothesisGeneratorNode
from src.state import new_state
from src.tools.rag import IndexDocument

DIAGNOSIS_PATH = "reports/error_diagnosis_0.json"
ERROR_DIAGNOSIS = {
    "iteration": 0,
    "root_cause": "overfitting",
    "confidence": 0.7,
    "evidence": ["fold scores range from 0.72 to 0.90"],
    "recommended_focus": "regularization",
}

FAKE_INDEX_DOCUMENT = IndexDocument(
    id="doc-1",
    text="A write-up describing heavy L2 regularization on this competition.",
    source="kaggle-forum-post-1",
    problem_type=["binary_classification"],
    methods_used=["lightgbm", "l2_regularization"],
    dataset_characteristics=["tabular"],
    key_findings="Heavier regularization closed the CV gap.",
    relevance_score=0.8,
)

VALID_HYPOTHESES: dict[str, Any] = {
    "hypotheses": [
        {
            "id": "stronger_regularization",
            "statement": "Raising L2 regularization will close the fold-score gap.",
            "rationale": "Fold variance is the dominant signal in the diagnosis.",
            "priority": 1,
            "expected_impact": "high",
            "addresses_root_cause": "overfitting",
        },
        {
            "id": "fewer_estimators",
            "statement": "Capping n_estimators will reduce memorization.",
            "rationale": "The search space allows very high capacity.",
            "priority": 2,
            "expected_impact": "medium",
            "addresses_root_cause": "overfitting",
        },
    ]
}

_REMOVED = object()


def _hypotheses_response(hypotheses: Any = _REMOVED) -> str:
    data = copy.deepcopy(VALID_HYPOTHESES)
    if hypotheses is not _REMOVED:
        data["hypotheses"] = hypotheses
    return json.dumps(data)


def _hypothesis(**overrides: Any) -> dict[str, Any]:
    entry = copy.deepcopy(VALID_HYPOTHESES["hypotheses"][0])
    entry.update(overrides)
    return entry


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=_hypotheses_response())
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
    instance.read_json.side_effect = lambda path: _artifact_for(path)
    instance.write_json.return_value = "/workspace/reports/hypotheses_0.json"
    with (
        patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls,
        patch("src.nodes.llm.hypothesis_generator.WorkspaceManager") as mock_wm_cls_node,
    ):
        mock_wm_cls.return_value = instance
        mock_wm_cls_node.return_value = instance
        yield mock_wm_cls, instance


@pytest.fixture
def rag_store() -> MagicMock:
    store = MagicMock()
    store.query.return_value = [FAKE_INDEX_DOCUMENT]
    return store


def _artifact_for(path: str) -> dict[str, Any]:
    if path != DIAGNOSIS_PATH:
        raise OSError(f"no such artifact: {path}")
    return copy.deepcopy(ERROR_DIAGNOSIS)


def _build_state(current_iteration: int = 0) -> Any:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    return state


def _written_artifact(workspace_instance: MagicMock) -> dict[str, Any]:
    args, _ = workspace_instance.write_json.call_args
    return args[1]


def _human_message(mock_llm: MagicMock) -> str:
    return str(mock_llm.invoke.call_args[0][0][-1].content)


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("hypothesis_generator")

    assert config.name == "hypothesis_generator"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "reports/hypotheses_{iteration}.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("hypothesis_generator", "v1")
    assert prompt.strip() != ""
    assert "# System prompt — hypothesis_generator" in prompt


def test_zero_arg_construction_leaves_the_rag_store_unbuilt(
    patched_llm_factory, patched_settings
) -> None:
    """`resolve_node` constructs every node with `cls()`; building a `RagStore`
    eagerly would open a Chroma connection at graph-build time."""
    node = HypothesisGeneratorNode()

    assert node._rag_store is None


# -- the RAG query (Done-when: queries the RagStore before producing hypotheses) --


def test_queries_the_rag_store_before_writing_hypotheses(
    patched_llm_factory, patched_settings, mock_workspace_manager, rag_store: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    recorder = MagicMock()
    recorder.attach_mock(rag_store, "rag_store")
    recorder.attach_mock(workspace_instance, "workspace")
    node = HypothesisGeneratorNode(rag_store=rag_store)
    state = _build_state()

    node(state)

    rag_store.query.assert_called_once()
    assert state["competition_name"] in rag_store.query.call_args[0][0]
    call_names = [call[0] for call in recorder.mock_calls]
    assert call_names.index("rag_store.query") < call_names.index("workspace.write_json")


def test_rag_query_incorporates_the_diagnosed_root_cause(
    patched_llm_factory, patched_settings, mock_workspace_manager, rag_store: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = lambda path: (
        copy.deepcopy(ERROR_DIAGNOSIS) | {"root_cause": "underfitting"}
    )
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(_build_state())

    query = rag_store.query.call_args[0][0]
    assert "underfitting" in query
    assert _written_artifact(workspace_instance)["rag_query"] == query


def test_rag_query_falls_back_to_a_generic_query_without_a_diagnosis(
    patched_llm_factory, patched_settings, mock_workspace_manager, rag_store: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(_build_state())

    query = rag_store.query.call_args[0][0]
    assert query == "previously tried approaches and failures for comp"


def test_root_cause_outside_the_vocabulary_is_not_interpolated_into_the_query(
    patched_llm_factory, patched_settings, mock_workspace_manager, rag_store: MagicMock
) -> None:
    """An upstream artifact is not a trusted input: only a token from the pinned
    vocabulary is allowed into the retrieval query."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = lambda path: (
        copy.deepcopy(ERROR_DIAGNOSIS) | {"root_cause": "something the model invented"}
    )
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(_build_state())

    assert "something the model invented" not in rag_store.query.call_args[0][0]


def test_rag_findings_and_diagnosis_are_injected_into_the_human_message(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
) -> None:
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(_build_state())

    content = _human_message(mock_llm)
    assert "## Error diagnosis" in content
    assert "## Prior attempts (RAG)" in content
    assert FAKE_INDEX_DOCUMENT.key_findings in content
    assert '"root_cause": "overfitting"' in content


def test_empty_rag_results_degrade_to_the_placeholder(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
) -> None:
    _, workspace_instance = mock_workspace_manager
    rag_store.query.return_value = []
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(_build_state())

    assert "No relevant findings found in the RAG store." in _human_message(mock_llm)
    assert _written_artifact(workspace_instance)["prior_attempts_considered"] == 0


def test_missing_error_diagnosis_degrades_to_a_placeholder(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(_build_state())

    assert "(error diagnosis not yet available)" in _human_message(mock_llm)
    workspace_instance.write_json.assert_called_once()


# -- the hypotheses artifact --


def test_hypotheses_are_stored_sorted_by_priority(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
) -> None:
    """ "Prioritized" is a property of the artifact, not of the response order."""
    _, workspace_instance = mock_workspace_manager
    mock_llm.invoke.return_value = AIMessage(
        content=_hypotheses_response(
            [
                _hypothesis(id="third", priority=3),
                _hypothesis(id="first", priority=1),
                _hypothesis(id="second", priority=2),
            ]
        )
    )
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(_build_state())

    hypotheses = _written_artifact(workspace_instance)["hypotheses"]
    assert [h["priority"] for h in hypotheses] == [1, 2, 3]
    assert [h["id"] for h in hypotheses] == ["first", "second", "third"]


def test_artifact_records_the_query_and_the_prior_attempt_count(
    patched_llm_factory, patched_settings, mock_workspace_manager, rag_store: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    rag_store.query.return_value = [FAKE_INDEX_DOCUMENT, FAKE_INDEX_DOCUMENT]
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(_build_state())

    artifact = _written_artifact(workspace_instance)
    assert artifact["iteration"] == 0
    assert artifact["prior_attempts_considered"] == 2
    assert "overfitting" in artifact["rag_query"]


def test_call_writes_under_the_current_iteration_number(
    patched_llm_factory, patched_settings, mock_workspace_manager, rag_store: MagicMock
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_json.side_effect = OSError("boom")
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(_build_state(current_iteration=2))

    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "reports/hypotheses_2.json"
    assert args[1]["iteration"] == 2


def test_extra_keys_in_a_hypothesis_are_dropped(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
) -> None:
    _, workspace_instance = mock_workspace_manager
    mock_llm.invoke.return_value = AIMessage(
        content=_hypotheses_response([_hypothesis(cost_estimate="cheap")])
    )
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(_build_state())

    hypothesis = _written_artifact(workspace_instance)["hypotheses"][0]
    assert set(hypothesis) == {
        "id",
        "statement",
        "rationale",
        "priority",
        "expected_impact",
        "addresses_root_cause",
    }


def test_build_output_state_returns_no_lab_state_field(
    patched_llm_factory, patched_settings, mock_workspace_manager, rag_store: MagicMock
) -> None:
    node = HypothesisGeneratorNode(rag_store=rag_store)

    delta = node(_build_state())

    assert set(delta) == {"messages"}


# -- validation rejects --


@pytest.mark.parametrize("hypotheses", [[], "not a list", None, [_hypothesis()] * 6])
def test_malformed_hypotheses_list_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
    hypotheses: Any,
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_hypotheses_response(hypotheses))
    node = HypothesisGeneratorNode(rag_store=rag_store)

    with pytest.raises(ValueError, match="hypothesis_generator"):
        node(_build_state())


@pytest.mark.parametrize(
    "priorities", [[1, 3], [1, 1], [0, 1], [2, 3]], ids=["gap", "duplicate", "zero", "offset"]
)
def test_priorities_not_covering_one_to_n_are_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
    priorities: list[int],
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_hypotheses_response(
            [_hypothesis(id=f"h{p}-{i}", priority=p) for i, p in enumerate(priorities)]
        )
    )
    node = HypothesisGeneratorNode(rag_store=rag_store)

    with pytest.raises(ValueError, match="priority"):
        node(_build_state())


def test_ids_differing_only_in_case_are_rejected_as_duplicates(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_hypotheses_response(
            [_hypothesis(id="Regularize", priority=1), _hypothesis(id="regularize", priority=2)]
        )
    )
    node = HypothesisGeneratorNode(rag_store=rag_store)

    with pytest.raises(ValueError, match="duplicate ids"):
        node(_build_state())


@pytest.mark.parametrize("impact", ["huge", "HIGH", None, 1])
def test_expected_impact_outside_the_enum_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
    impact: Any,
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_hypotheses_response([_hypothesis(expected_impact=impact)])
    )
    node = HypothesisGeneratorNode(rag_store=rag_store)

    with pytest.raises(ValueError, match="expected_impact"):
        node(_build_state())


def test_addresses_root_cause_outside_the_vocabulary_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_hypotheses_response([_hypothesis(addresses_root_cause="bad data")])
    )
    node = HypothesisGeneratorNode(rag_store=rag_store)

    with pytest.raises(ValueError, match="addresses_root_cause"):
        node(_build_state())


@pytest.mark.parametrize("statement", ["", "   ", None, 42])
def test_blank_statement_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
    statement: Any,
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_hypotheses_response([_hypothesis(statement=statement)])
    )
    node = HypothesisGeneratorNode(rag_store=rag_store)

    with pytest.raises(ValueError, match="statement"):
        node(_build_state())


def test_boolean_priority_is_rejected(
    patched_llm_factory,
    patched_settings,
    mock_workspace_manager,
    rag_store: MagicMock,
    mock_llm: MagicMock,
) -> None:
    """`isinstance(True, int)` is `True`, so an unguarded read would accept
    `true` as the rank 1."""
    mock_llm.invoke.return_value = AIMessage(
        content=_hypotheses_response([_hypothesis(priority=True)])
    )
    node = HypothesisGeneratorNode(rag_store=rag_store)

    with pytest.raises(ValueError, match="priority"):
        node(_build_state())


def test_non_integer_current_iteration_coerces_to_zero_in_the_output_path(
    patched_llm_factory, patched_settings, mock_workspace_manager, rag_store: MagicMock
) -> None:
    """`isinstance(True, int)` is `True`, so an unguarded read would file the
    hypotheses at `reports/hypotheses_True.json` next to an `"iteration": 0`
    body."""
    _, workspace_instance = mock_workspace_manager
    state = _build_state()
    state["current_iteration"] = True
    node = HypothesisGeneratorNode(rag_store=rag_store)

    node(state)

    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "reports/hypotheses_0.json"
    assert args[1]["iteration"] == 0
