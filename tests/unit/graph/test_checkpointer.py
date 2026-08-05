"""Unit test for the SQLite checkpointer's resume-after-restart behavior.

No real node implementations exist yet, so every node resolves to a
`NoOpNode` (see `src.graph.node_resolver`). `node_resolver.resolve_node` is
monkeypatched to wrap each resolved node in a call counter — this makes
"did this node actually re-execute" observable without needing a real node.
"""

from pathlib import Path

import pytest

from src.graph import node_resolver
from src.graph.builder import GraphBuilder
from src.state import new_state


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
    state = new_state("comp", "/tmp/comp")
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
