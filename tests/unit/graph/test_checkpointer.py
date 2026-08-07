"""Unit test for the SQLite checkpointer's resume-after-restart behavior.

Most nodes still resolve to a `NoOpNode` (see `src.graph.node_resolver`), but
phase1's `data_analyst` (T-013) is a real `LLMNode` subclass now, so these
tests — which call `GraphBuilder().build()` and then `.invoke()` for real —
mock the LLM call (`src.nodes.llm.base.LLMFactory`) and set fake values for
`config/settings.yaml`'s five `${...}`-interpolated env vars, mirroring
`tests/integration/phases/test_phase_subgraphs_smoke.py`'s convention.
`node_resolver.resolve_node` is separately monkeypatched to wrap each
resolved node in a call counter — this makes "did this node actually
re-execute" observable without needing a real node.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from src.graph import node_resolver
from src.graph.builder import GraphBuilder
from src.llm.factory import LLMFactory
from src.state import new_state

# Satisfies data_analyst's requirement of exactly one fenced ```python block
# (config/prompts/data_analyst/v1.md); harmless for any other real LLMNode's
# default `_write_output`, which just writes the raw text as-is.
_MOCK_LLM_CONTENT = (
    "## Smoke test narrative\n\nMocked LLM response for the checkpointer test.\n\n"
    '```python\nprint("checkpointer test")\n```\n'
)

# problem_framer and leakage_auditor (T-014) are structured-JSON nodes, not
# fenced-python nodes — `_MOCK_LLM_CONTENT` above would fail their JSON
# parsing, so the mock LLM dispatches on which node is calling (identified by
# the `# System prompt — {name}` header text every node's system prompt
# starts with, see the `_llm_side_effect` docstring below).
_MOCK_PROBLEM_DEFINITION = json.dumps(
    {
        "problem_type": "binary_classification",
        "success_metric": "roc_auc",
        "constraints": [],
    }
)
_MOCK_LEAKAGE_AUDIT = json.dumps({"leaks": [], "severity": "none", "blocks_progression": False})


def _llm_side_effect(messages: list[BaseMessage]) -> AIMessage:
    """Dispatch mocked LLM output by inspecting the outgoing SystemMessage's
    content for the calling node's own `# System prompt — {name}` header
    line (see `config/prompts/{name}/v1.md`) — matching the *header*
    specifically, not a bare substring, because leakage_auditor's own
    prompt prose mentions "problem_framer" (its upstream node), which would
    otherwise mis-route it. Falls back to the data_analyst-shaped
    `_MOCK_LLM_CONTENT` for every other node."""
    system_content = str(messages[0].content) if messages else ""
    if "System prompt — problem_framer" in system_content:
        return AIMessage(content=_MOCK_PROBLEM_DEFINITION)
    if "System prompt — leakage_auditor" in system_content:
        return AIMessage(content=_MOCK_LEAKAGE_AUDIT)
    return AIMessage(content=_MOCK_LLM_CONTENT)


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GROQ_API_KEY",
        "KAGGLE_USERNAME",
        "KAGGLE_KEY",
    ):
        monkeypatch.setenv(var, "fake-value-for-settings-load")
    LLMFactory._settings = None

    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = _llm_side_effect

    with patch("src.nodes.llm.base.LLMFactory") as mock_factory:
        mock_factory.get.return_value = mock_llm
        yield

    LLMFactory._settings = None


def _install_counting_resolver(monkeypatch: pytest.MonkeyPatch, call_counts: dict) -> None:
    original_resolve_node = node_resolver.resolve_node

    def counting_resolve_node(name: str):
        node = original_resolve_node(name)

        def _counting(state: dict) -> dict:
            call_counts[name] = call_counts.get(name, 0) + 1
            return node(state)

        return _counting

    monkeypatch.setattr(node_resolver, "resolve_node", counting_resolve_node)


def test_resume_after_restart_does_not_rerun_completed_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_counts: dict[str, int] = {}
    _install_counting_resolver(monkeypatch, call_counts)

    run_id = "resume-test-run"
    config = {"configurable": {"thread_id": run_id}}

    # First "process": build the graph and run it up to the phase1 interrupt.
    first_graph = GraphBuilder().build(run_id=run_id, runs_dir=tmp_path)
    state = new_state("comp", str(tmp_path / "workspace"))
    first_graph.invoke(state, config=config)

    assert call_counts.get("data_analyst", 0) == 1
    assert "literature_researcher" not in call_counts  # phase2 hasn't run yet

    # Second "process" (simulated restart): a brand-new GraphBuilder pointed at
    # the same checkpoint DB + thread_id.
    second_graph = GraphBuilder().build(run_id=run_id, runs_dir=tmp_path)
    second_graph.invoke(None, config=config)

    # Phase 1 must not re-execute on resume.
    assert call_counts.get("data_analyst", 0) == 1
    assert call_counts.get("problem_framer", 0) == 1
    # Execution continued from phase2 onward.
    assert call_counts.get("literature_researcher", 0) == 1
    assert call_counts.get("web_researcher", 0) == 1


def test_checkpoint_db_created_at_expected_path(tmp_path: Path) -> None:
    run_id = "path-test-run"
    GraphBuilder().build(run_id=run_id, runs_dir=tmp_path)

    assert (tmp_path / run_id / "checkpoint.db").exists()
