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
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from src.graph.node_resolver import resolve_node
from src.graph.phases import PHASE_ORDER
from src.state import new_state

# Generic enough to satisfy any real `LLMNode` subclass's `_write_output`:
# the default implementation just writes the raw text, `data_analyst`
# additionally requires exactly one fenced ```python block per
# config/prompts/data_analyst/v1.md (any stdout is fine, it's embedded as-is),
# and `validation_strategist` additionally requires that block's *stdout* to
# be a single JSON object with `strategy`/`n_folds`/`fold_indices`/`seed`
# keys per config/prompts/validation_strategist/v1.md — printing that JSON is
# harmless for every other node, which never parses stdout.
_MOCK_LLM_CONTENT = (
    "## Smoke test narrative\n\nMocked LLM response for the phase-subgraph smoke test.\n\n"
    "```python\n"
    "import json\n"
    'print(json.dumps({"strategy": "stratified", "n_folds": 1, "seed": 0, '
    '"fold_indices": [{"train": [0], "val": [1]}]}))\n'
    "```\n"
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
# baseline_designer (T-020) is also a structured-JSON node — its own
# `_extract_json`/`_validate_design` would reject `_MOCK_LLM_CONTENT`'s fenced
# ```python narrative shape, so it needs its own dispatch entry too.
_MOCK_BASELINE_DESIGN = json.dumps(
    {
        "model": "logistic_regression",
        "hyperparameters": {},
        "features": "all",
        "target_column": "target",
    }
)
# literature_researcher/web_researcher (T-017) are also structured-JSON nodes
# (a JSON array, one entry per searched source) — paired below with the
# search-client mocks in `_mock_llm`, which make both nodes see zero sources,
# so an empty array is always the correct, schema-valid extraction response.
# memory_manager (T-019) reuses this same constant: its own output is also a
# top-level JSON array (one entry per consolidated cluster, see
# config/prompts/memory_manager/v1.md), and its mocked `RagStore.query()`
# below is set to return zero candidates, so an empty array is the correct,
# schema-valid response for it too (`_build_consolidated_documents`'s
# documented empty-candidates short circuit).
_MOCK_EMPTY_EXTRACTION = json.dumps([])

# competition_analyst (T-018) is also a structured-JSON node, but its output
# is a top-level JSON *array* of per-kernel extractions, not an object — see
# config/prompts/competition_analyst/v1.md. One entry per fake kernel
# returned by `_FAKE_KERNELS` below (see the `_mock_kernel_lister` fixture).
_MOCK_COMPETITION_ANALYSIS = json.dumps(
    [
        {
            "index": 1,
            "problem_type": ["binary_classification"],
            "methods_used": ["gradient boosting"],
            "dataset_characteristics": [],
            "key_findings": "Smoke-test kernel extraction.",
            "relevance_score": 0.5,
        }
    ]
)

# solution_architect (T-021) is also a structured-JSON node — its own
# `_extract_json`/`_validate_solution_plan` require `model_families`/`order`
# (non-empty list[str]), `ensembling_strategy`/`rationale` (non-empty str), and
# `realistic_ceiling` (dict with `metric`/`target_score`/`rationale`) — see
# config/prompts/solution_architect/v1.md.
_MOCK_SOLUTION_PLAN = json.dumps(
    {
        "model_families": ["gradient_boosting", "logistic_regression"],
        "order": ["gradient_boosting", "logistic_regression"],
        "ensembling_strategy": "single best model for v1, no ensembling",
        "realistic_ceiling": {
            "metric": "roc_auc",
            "target_score": 0.9,
            "rationale": "Smoke-test placeholder ceiling.",
        },
        "rationale": "Smoke-test placeholder solution plan.",
    }
)

# feature_engineer (T-022) is also a structured-JSON node — its own
# `_extract_json`/`_validate_feature_spec` require exactly `encodings`/
# `null_handling`/`interactions` list keys (see config/prompts/
# feature_engineer/v1.md), which `_MOCK_LLM_CONTENT`'s fenced ```python
# narrative shape would fail, so it needs its own dispatch entry too.
_MOCK_FEATURE_SPEC = json.dumps(
    {
        "encodings": [{"column": "cat1", "method": "one_hot"}],
        "null_handling": [{"column": "num1", "strategy": "median_impute"}],
        "interactions": [],
    }
)

# classical_ml_specialist (T-024) is also a structured-JSON node — its own
# `extract_json_object`/`validate_experiment_design` (src/nodes/llm/
# _experiment_design.py) require `model_family` (one of the four supported
# families), a non-empty `search_space` of `{"type": ...}` objects, and
# `fixed_params`/`preprocessing`/`rationale` (see config/prompts/
# classical_ml_specialist/v1.md), which `_MOCK_LLM_CONTENT`'s fenced ```python
# narrative shape would fail, so it needs its own dispatch entry too. Every
# input it reads degrades to a placeholder in this standalone-phase run
# (`feature_spec_path`/`solution_plan_path`/`validation_config_path` are all
# `""` in `new_state`), so no fixture seeding is needed.
_MOCK_CLASSICAL_ML_DESIGN = json.dumps(
    {
        "model_family": "lightgbm",
        "search_space": {
            "n_estimators": {"type": "int", "low": 100, "high": 1000},
            "boosting_type": {"type": "categorical", "choices": ["gbdt", "dart"]},
        },
        "fixed_params": {},
        "preprocessing": [],
        "rationale": "Smoke-test placeholder experiment design.",
    }
)

_FAKE_KERNELS = [
    {
        "ref": "smoke-user/smoke-kernel",
        "title": "Smoke test kernel",
        "author": "smoke-user",
        "total_votes": 1,
        "url": "https://www.kaggle.com/code/smoke-user/smoke-kernel",
    }
]


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
    if "System prompt — baseline_designer" in system_content:
        return AIMessage(content=_MOCK_BASELINE_DESIGN)
    if "System prompt — solution_architect" in system_content:
        return AIMessage(content=_MOCK_SOLUTION_PLAN)
    if "System prompt — feature_engineer" in system_content:
        return AIMessage(content=_MOCK_FEATURE_SPEC)
    if "System prompt — classical_ml_specialist" in system_content:
        return AIMessage(content=_MOCK_CLASSICAL_ML_DESIGN)
    if "System prompt — literature_researcher" in system_content:
        return AIMessage(content=_MOCK_EMPTY_EXTRACTION)
    if "System prompt — web_researcher" in system_content:
        return AIMessage(content=_MOCK_EMPTY_EXTRACTION)
    if "System prompt — competition_analyst" in system_content:
        return AIMessage(content=_MOCK_COMPETITION_ANALYSIS)
    if "System prompt — memory_manager" in system_content:
        return AIMessage(content=_MOCK_EMPTY_EXTRACTION)
    return AIMessage(content=_MOCK_LLM_CONTENT)


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock only the LLM network call for every real `LLMNode` a phase might
    resolve to, so this smoke test never makes a real network call or
    depends on provider API keys actually working — mirrors `tests/unit/
    nodes/llm/test_base.py`'s mocking convention, patched at
    `src.nodes.llm.base`'s import location. `Settings.load()` itself
    (read by `LLMNode.__init__` and, for `data_analyst`, by
    `code_executor.execute`'s real-subprocess path) still runs for real
    against `config/settings.yaml`, which requires all five
    `${...}`-interpolated env vars to be *set* (any non-empty value works,
    matching the fake-key convention in `tests/tools/test_code_executor.py`
    and `tests/unit/llm/test_factory.py`) even though the LLM call they'd
    normally authenticate is mocked away.

    `literature_researcher`/`web_researcher` (T-017) additionally build their
    own `SearchClient` (arxiv/Semantic Scholar/Tavily, real network) and
    `RagStore` (real Chroma client pointed at `config/settings.yaml`'s
    `workspace.chroma_host`/`chroma_port`, unreachable in this test
    environment) on first use — both are mocked here too so this smoke test
    stays network-free end to end. With the search clients returning no
    sources, `_MOCK_EMPTY_EXTRACTION` above is the schema-valid response for
    both nodes regardless of the (unused) query text.

    Two more real-world-facing calls need the same treatment for
    `competition_analyst` (T-018), whose defaults are real network/service
    clients when not injected (`resolve_node` always constructs it with no
    args): its default `kernel_lister` (the bare `list_top_kernels` function,
    which would otherwise hit the real Kaggle API and 401 on the fake
    credentials above) and its lazily-constructed `RagStore` (which would
    otherwise try to reach the Docker `chroma` service at
    `config/settings.yaml`'s `workspace.chroma_host`/`chroma_port`, unreachable
    here). Both are patched at their `src.nodes.llm.competition_analyst` import
    location, mirroring the `LLMFactory` patch above.

    `memory_manager` (T-019) runs last in phase2_research and, like
    `competition_analyst`, is always constructed with no args by
    `resolve_node`, so its lazily-built `RagStore` (`src.nodes.llm.
    memory_manager.RagStore`) is patched here too. Its `.query()` is set to
    return an empty list so the node takes its own documented
    empty-candidates short circuit — see `_MOCK_EMPTY_EXTRACTION`'s comment
    above for why that makes `"[]"` the correct mocked LLM response for it.

    `solution_architect` (T-021, phase4_design) has the identical
    lazily-constructed `RagStore` shape (`_ensure_rag_store`) as
    `web_researcher`/`memory_manager` — patched at `src.nodes.llm.
    solution_architect.RagStore` with `.query()` returning an empty list for
    the same reason.
    """
    for var in (
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GROQ_API_KEY",
        "KAGGLE_USERNAME",
        "KAGGLE_KEY",
    ):
        monkeypatch.setenv(var, "smoke-test-fake-value")

    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = _llm_side_effect

    with (
        patch("src.nodes.llm.base.LLMFactory") as mock_factory,
        patch("src.nodes.llm.literature_researcher.LiteratureSearchClient") as mock_lit_client,
        patch("src.nodes.llm.web_researcher.WebSearchClient") as mock_web_client,
        patch("src.nodes.llm.literature_researcher.RagStore") as mock_lit_rag_store,
        patch("src.nodes.llm.web_researcher.RagStore") as mock_web_rag_store,
        patch(
            "src.nodes.llm.competition_analyst.list_top_kernels",
            return_value=_FAKE_KERNELS,
        ),
        patch("src.nodes.llm.competition_analyst.RagStore") as mock_rag_store_cls,
        patch("src.nodes.llm.memory_manager.RagStore") as mock_memory_rag_store,
        patch("src.nodes.llm.solution_architect.RagStore") as mock_sa_rag_store,
        patch("src.nodes.compute.baseline_runner.mlflow") as mock_mlflow,
    ):
        mock_factory.get.return_value = mock_llm
        mock_lit_client.return_value.search.return_value = []
        mock_web_client.return_value.search.return_value = []
        mock_lit_rag_store.return_value = MagicMock()
        mock_web_rag_store.return_value = MagicMock()
        mock_rag_store_cls.return_value = MagicMock()
        mock_memory_rag_store.return_value = MagicMock()
        mock_memory_rag_store.return_value.query.return_value = []
        mock_sa_rag_store.return_value = MagicMock()
        mock_sa_rag_store.return_value.query.return_value = []
        mock_mlflow.start_run.return_value.__enter__.return_value = MagicMock()
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
    raw_dir = tmp_path / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "train.csv").write_text(
        "feature1,target\n1,0\n2,1\n3,0\n4,1\n5,0\n", encoding="utf-8"
    )
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
