"""Unit test for the SQLite checkpointer's resume-after-restart behavior.

These tests call `GraphBuilder().build()` and then `.invoke()` for real, so
every real `LLMNode` a phase resolves to is driven by the shared network-free
mock set in `tests/fixtures/graph_mocks.py` (extracted there in B-001; the
phase-subgraph smoke test is the other consumer). This test opts into
`analysis_critic_pass=True`: it counts *graph-driven* node executions, and a
mocked-away `pass` verdict keeps `analysis_critic`'s legitimate iterate
retries (CLAUDE.md invariant #5) from being miscounted as phase re-execution.

`node_resolver.resolve_node` is separately monkeypatched to wrap each resolved
node in a call counter — this makes "did this node actually re-execute"
observable without needing a real node. It is patched at *two* locations (see
`_install_counting_resolver`) so the counts do not depend on module import
order.
"""

from pathlib import Path

import pytest

from src.graph import node_resolver
from src.graph.builder import GraphBuilder
from src.llm.factory import LLMFactory
from src.state import new_state
from tests.fixtures.graph_mocks import graph_llm_mocks, seed_raw_train_csv, set_fake_provider_env


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    set_fake_provider_env(monkeypatch, "fake-value-for-settings-load")
    LLMFactory._settings = None

    with graph_llm_mocks(analysis_critic_pass=True):
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

    # `builder.py` resolves through the module attribute (src/graph/builder.py:70-73),
    # but `analysis_critic` binds `resolve_node` into its own namespace at import time
    # (src/nodes/llm/analysis_critic.py:31) — its module docstring (lines 16-20) requires
    # unit tests to patch it there, so B-001 does NOT change src/. Patching BOTH makes the
    # count identical whether or not `analysis_critic` was already imported earlier in the session.
    monkeypatch.setattr(node_resolver, "resolve_node", counting_resolve_node)
    monkeypatch.setattr("src.nodes.llm.analysis_critic.resolve_node", counting_resolve_node)


def test_resume_after_restart_does_not_rerun_completed_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call_counts: dict[str, int] = {}
    _install_counting_resolver(monkeypatch, call_counts)

    run_id = "resume-test-run"
    config = {"configurable": {"thread_id": run_id}}
    workspace_path = tmp_path / "workspace"
    # Seeded BEFORE the first invoke: phase 1's `validation_strategist` freezes
    # validation/fold_config.json (write-once, CLAUDE.md invariant #1), so unlike the
    # smoke test's `_seed_phase3_baseline_fixtures` nothing may be seeded after the fact.
    # Only the dataset is seeded here; the folds come from the real phase-1 run.
    seed_raw_train_csv(workspace_path)

    # First "process": build the graph and run it up to the phase1 interrupt.
    first_graph = GraphBuilder().build(run_id=run_id, runs_dir=tmp_path)
    first_graph.invoke(new_state("comp", str(workspace_path)), config=config)

    assert call_counts.get("data_analyst", 0) == 1
    assert "literature_researcher" not in call_counts  # phase2 hasn't run yet
    assert first_graph.get_state(config).next == ("phase2_research",)

    # Second "process" (simulated restart): a brand-new GraphBuilder pointed at
    # the same checkpoint DB + thread_id.
    second_graph = GraphBuilder().build(run_id=run_id, runs_dir=tmp_path)
    second_graph.invoke(None, config=config)

    # Phase 1 must not re-execute on resume.
    assert call_counts.get("data_analyst", 0) == 1
    assert call_counts.get("problem_framer", 0) == 1
    assert call_counts.get("validation_strategist", 0) == 1
    assert call_counts.get("leakage_auditor", 0) == 1
    # phase2/3/4 each executed exactly once: `interrupt_after: false` on phases 2 and 3
    # means invoke(None) runs straight through to the phase-4 interrupt.
    assert call_counts.get("literature_researcher", 0) == 1
    assert call_counts.get("web_researcher", 0) == 1
    assert call_counts.get("competition_analyst", 0) == 1
    assert call_counts.get("memory_manager", 0) == 1
    assert call_counts.get("baseline_designer", 0) == 1
    assert call_counts.get("baseline_runner", 0) == 1
    assert call_counts.get("solution_architect", 0) == 1
    assert call_counts.get("feature_engineer", 0) == 1
    # analysis_critic is the last node of BOTH phase1_understanding and phase4_design
    # (config/phases/*.yaml), so 2 — not 1 — is correct.
    assert call_counts.get("analysis_critic", 0) == 2
    # Halted at phase 4's interrupt; phase 5 never runs, which is why no specialist
    # node needs a mock.
    assert second_graph.get_state(config).next == ("phase5_implementation",)


def test_checkpoint_db_created_at_expected_path(tmp_path: Path) -> None:
    run_id = "path-test-run"
    GraphBuilder().build(run_id=run_id, runs_dir=tmp_path)

    assert (tmp_path / run_id / "checkpoint.db").exists()
