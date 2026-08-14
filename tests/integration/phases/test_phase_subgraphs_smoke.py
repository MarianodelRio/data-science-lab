"""Integration smoke test: each of the 7 phase subgraphs, built standalone via
its `build(resolve_node)`, compiles and runs one full pass over a fresh
`new_state(...)` without raising.

Recommended by design.md's testing-strategy row for `src/graph/` — real
end-to-end coverage of the parallel fan-out/fan-in wiring (phase2) in
addition to the plain sequential phases. Most phase nodes are still
`NoOpNode` placeholders (no implementing task has landed yet), but as real
`LLMNode` subclasses land (e.g. `data_analyst`, T-013) `resolve_node` picks
them up for real per its by-convention discovery — so per design.md's
testing-strategy row for `src/nodes/llm/` ("integration: phase subgraph with
mock LLM"), the LLM call itself is mocked module-wide here; everything else
(config/prompt loading, `WorkspaceManager` file writes, `code_executor`
subprocess execution) runs for real against a tmp workspace.
"""

import importlib
import json

import pytest

from src.graph.node_resolver import resolve_node
from src.graph.phases import PHASE_ORDER
from src.state import new_state
from tests.fixtures.graph_mocks import graph_llm_mocks, seed_raw_train_csv, set_fake_provider_env


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enter the shared network-free mock set, which now lives in
    `tests/fixtures/graph_mocks.py` (extracted in B-001 so
    `tests/unit/graph/test_checkpointer.py` can reuse it) — see
    `graph_llm_mocks` for why each patch exists. No argument is passed, so
    `analysis_critic` stays undispatched and this suite keeps exercising the
    critic's iterate/forced-pass path exactly as before."""
    set_fake_provider_env(monkeypatch, "smoke-test-fake-value")

    with graph_llm_mocks():
        yield


def _seed_phase3_baseline_fixtures(tmp_path) -> None:
    """`phase3_baseline` is the first phase in this suite whose real node
    (`baseline_runner`, T-020) both executes a real subprocess (via
    `code_executor.execute`, unmocked here per this module's own convention)
    AND depends on artifacts a real Phase 1 run would already have produced
    (`validation/fold_config.json`, frozen by `validation_strategist`) —
    unlike every earlier real node in this suite, whose LLM-authored script
    content is itself supplied by the mocked LLM response and therefore
    needs no real upstream data. Exercised standalone (no Phase 1 run ahead
    of it, matching every other phase in this parametrized test), Phase 3
    has neither the frozen folds nor a real dataset to train on, so both are
    seeded directly here."""
    seed_raw_train_csv(tmp_path)
    validation_dir = tmp_path / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    (validation_dir / "fold_config.json").write_text(
        json.dumps(
            {
                "strategy": "stratified",
                "n_folds": 1,
                "seed": 0,
                "fold_indices": [{"train": [0, 1, 2], "val": [3, 4]}],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("stem", PHASE_ORDER)
def test_phase_subgraph_compiles_and_runs(stem: str, tmp_path) -> None:
    module = importlib.import_module(f"src.graph.phases.{stem}")
    compiled = module.build(resolve_node)

    if stem == "phase3_baseline":
        _seed_phase3_baseline_fixtures(tmp_path)

    state = new_state("comp", str(tmp_path))
    result = compiled.invoke(state)

    assert result["competition_name"] == "comp"

    if stem == "phase5_implementation":
        # `classical_ml_specialist` (T-024) is the first real node in this phase
        # to produce a file — assert the artifact actually landed, not just that
        # the subgraph ran without raising.
        design_path = tmp_path / "experiments" / "exp_0" / "design.json"
        assert design_path.is_file()
        design = json.loads(design_path.read_text(encoding="utf-8"))
        assert design["specialist"] == "classical_ml_specialist"
        assert design["cv_strategy_ref"] == "validation/fold_config.json"
        assert "n_estimators" in design["search_space"]

        # `code_critic` (T-030) is a real node now, not a `NoOpNode` — its
        # verdict record landing proves the live edge. Like `analysis_critic`,
        # it is deliberately left undispatched in `graph_mocks._DISPATCH`, so
        # the fallback mock response normalizes to `iterate` and this is the
        # only place the real forced-pass path runs through a real graph.
        assert (tmp_path / "reports" / "code_critic_verdicts_iter0.json").is_file()


def test_phase5_subgraph_routes_neural_plan_to_deep_learning_specialist(tmp_path) -> None:
    """The parametrized phase-5 case above runs on an unseeded workspace, so
    `specialist_selector`'s keyword precedence falls through to its default
    (`classical_ml_specialist`). This seeds a plan whose signal is neural, so the
    real selector takes its deep-learning branch — covering keyword branch ->
    `resolve_node` -> real node -> file on disk, which nothing else exercises now
    that `test_specialist_selector.py`'s NoOp test routes through a sentinel name
    no module implements.
    """
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "problem_definition.json").write_text(
        json.dumps({"problem_type": "tabular_binary_classification"}), encoding="utf-8"
    )
    design_dir = tmp_path / "design" / "iteration_0"
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "solution_plan.json").write_text(
        json.dumps(
            {
                "model_families": ["neural network"],
                "order": ["neural network"],
                "ensembling_strategy": "",
                "rationale": "Deep learning on a large tabular dataset.",
            }
        ),
        encoding="utf-8",
    )

    module = importlib.import_module("src.graph.phases.phase5_implementation")
    compiled = module.build(resolve_node)

    state = new_state("comp", str(tmp_path))
    state["problem_definition_path"] = "reports/problem_definition.json"
    state["solution_plan_path"] = "design/iteration_0/solution_plan.json"
    compiled.invoke(state)

    design_path = tmp_path / "experiments" / "exp_0" / "design.json"
    assert design_path.is_file()
    design = json.loads(design_path.read_text(encoding="utf-8"))
    assert design["specialist"] == "deep_learning_specialist"
    assert design["cv_strategy_ref"] == "validation/fold_config.json"
    assert "n_layers" in design["search_space"]


def test_phase5_subgraph_routes_forecasting_plan_to_timeseries_specialist(tmp_path) -> None:
    """The timeseries branch of `specialist_selector`'s keyword precedence — the
    first of its four, so nothing else can shadow it — through `resolve_node` to
    the real `timeseries_specialist` node and a design file on disk.

    This is the coverage `test_specialist_selector.py` used to provide by routing a
    forecasting plan through the real `resolve_node`; once T-027 landed, doing that
    in a unit test would construct a real chat model, so the real-path coverage
    moved here where the LLM is mocked module-wide.
    """
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "problem_definition.json").write_text(
        json.dumps({"problem_type": "time_series_forecasting"}), encoding="utf-8"
    )
    design_dir = tmp_path / "design" / "iteration_0"
    design_dir.mkdir(parents=True, exist_ok=True)
    (design_dir / "solution_plan.json").write_text(
        json.dumps(
            {
                "model_families": ["prophet"],
                "order": ["prophet"],
                "ensembling_strategy": "",
                "rationale": "Daily demand forecast with weekly seasonality.",
            }
        ),
        encoding="utf-8",
    )

    module = importlib.import_module("src.graph.phases.phase5_implementation")
    compiled = module.build(resolve_node)

    state = new_state("comp", str(tmp_path))
    state["problem_definition_path"] = "reports/problem_definition.json"
    state["solution_plan_path"] = "design/iteration_0/solution_plan.json"
    compiled.invoke(state)

    design_path = tmp_path / "experiments" / "exp_0" / "design.json"
    assert design_path.is_file()
    design = json.loads(design_path.read_text(encoding="utf-8"))
    assert design["specialist"] == "timeseries_specialist"
    assert design["cv_strategy_ref"] == "validation/fold_config.json"
    assert design["model_family"] == "arima"
    assert "order" in design["search_space"]


def test_phase2_fan_in_join_node_runs_exactly_once() -> None:
    """`literature_researcher`/`web_researcher` fan out in parallel and fan
    back in to `competition_analyst` (see phase2_research.yaml's
    `parallel_nodes`). Nothing else in the suite would catch a future wiring
    regression (e.g. an accidental duplicated edge into the join node)
    causing `competition_analyst` to execute more than once per run — this
    asserts the join node's call count directly, mirroring the
    call-counting pattern in `tests/unit/graph/test_checkpointer.py`.
    """
    call_counts: dict[str, int] = {}

    def counting_resolve_node(name: str):
        node = resolve_node(name)

        def _counting(state: dict) -> dict:
            call_counts[name] = call_counts.get(name, 0) + 1
            return node(state)

        return _counting

    module = importlib.import_module("src.graph.phases.phase2_research")
    compiled = module.build(counting_resolve_node)

    state = new_state("comp", "/tmp/comp")
    compiled.invoke(state)

    assert call_counts.get("literature_researcher", 0) == 1
    assert call_counts.get("web_researcher", 0) == 1
    assert call_counts.get("competition_analyst", 0) == 1
