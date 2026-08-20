"""Shared network-free mock set for tests that build and invoke the *real*
graph (`tests/unit/graph/test_checkpointer.py`) or a *real* phase subgraph
(`tests/integration/phases/test_phase_subgraphs_smoke.py`).

Extracted in B-001 from the smoke test, which was its sole owner until the
checkpointer test needed the same patch set to stop making live network calls.

This is the first Python module in `tests/fixtures/` — design.md's test-tree
sketch describes that directory as holding data (`datasets/`, `responses/`,
`workspaces/`). It is colocated here anyway because B-001's `folders:` allows
it and its two consumers live in different trees (`tests/unit/graph/` and
`tests/integration/phases/`), so neither can own it.

Only the outward-facing calls are mocked: the LLM itself, the research search
clients, `RagStore`, the Kaggle kernel lister and mlflow. Everything else
(config/prompt loading, `WorkspaceManager` file writes, `code_executor`
subprocess execution, the real sklearn baseline subprocess) runs for real.
"""

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage

# Generic enough to satisfy any real `LLMNode` subclass's `_write_output`:
# the default implementation just writes the raw text, `data_analyst`
# additionally requires exactly one fenced ```python block per
# config/prompts/data_analyst/v1.md (any stdout is fine, it's embedded as-is),
# and `validation_strategist` additionally requires that block's *stdout* to
# be a single JSON object with `strategy`/`n_folds`/`fold_indices`/`seed`
# keys per config/prompts/validation_strategist/v1.md — printing that JSON is
# harmless for every other node, which never parses stdout.
#
# NOTE for future editors: this constant is dual-purpose — it is both the
# generic fallback for undispatched nodes AND `validation_strategist`'s fold
# source. `validation_strategist` freezes the printed fold spec *verbatim*
# into `validation/fold_config.json` (CLAUDE.md invariant #1: write-once), and
# phase 3's real `baseline_runner` then trains a real sklearn
# `LogisticRegression` subprocess against those exact indices. The split below
# is therefore `[0, 1, 2]` / `[3, 4]`, matching the 5-row CSV written by
# `seed_raw_train_csv` so that both splits contain both classes: the earlier
# `{"train": [0], "val": [1]}` is a single-row, single-class training split
# and dies inside `sklearn/linear_model/_logistic.py`.
_MOCK_LLM_CONTENT = (
    "## Analysis narrative\n\nMocked fallback LLM response for graph-driven tests.\n\n"
    "```python\n"
    "import json\n"
    'print(json.dumps({"strategy": "stratified", "n_folds": 1, "seed": 0, '
    '"fold_indices": [{"train": [0, 1, 2], "val": [3, 4]}]}))\n'
    "```\n"
)

# problem_framer and leakage_auditor (T-014) are structured-JSON nodes, not
# fenced-python nodes — `_MOCK_LLM_CONTENT` above would fail their JSON
# parsing, so the mock LLM dispatches on which node is calling (identified by
# the `# System prompt — {name}` header text every node's system prompt
# starts with, see the `_make_llm_side_effect` docstring below).
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
# search-client mocks in `graph_llm_mocks`, which make both nodes see zero
# sources, so an empty array is always the correct, schema-valid extraction
# response. memory_manager (T-019) reuses this same constant: its own output
# is also a top-level JSON array (one entry per consolidated cluster, see
# config/prompts/memory_manager/v1.md), and its mocked `RagStore.query()`
# below is set to return zero candidates, so an empty array is the correct,
# schema-valid response for it too (`_build_consolidated_documents`'s
# documented empty-candidates short circuit).
_MOCK_EMPTY_EXTRACTION = json.dumps([])

# competition_analyst (T-018) is also a structured-JSON node, but its output
# is a top-level JSON *array* of per-kernel extractions, not an object — see
# config/prompts/competition_analyst/v1.md. One entry per fake kernel
# returned by `_FAKE_KERNELS` below (see the `graph_llm_mocks` patch set).
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

# feature_engineer (T-022, schema v2 in T-047) is also a structured-JSON node — its
# own `_extract_json`/`_validate_feature_spec` require a single `features` list whose
# every entry carries `columns`/`operation`/`params`/`fit_scope`/`rationale`, with
# `fit_scope` ∈ {"per_fold", "global"} required on every entry and forced to
# "per_fold" for the leakage-prone operation families (see config/prompts/
# feature_engineer/v2.md), which `_MOCK_LLM_CONTENT`'s fenced ```python narrative
# shape would fail, so it needs its own dispatch entry too. The two entries below
# are deliberately one of each: a stateless `global` transform, and one that matches
# a family, so both branches of the fit-scope guard run in the graph-driven tests.
_MOCK_FEATURE_SPEC = json.dumps(
    {
        "features": [
            {
                "columns": ["num1"],
                "operation": "log_transform",
                "params": {},
                "fit_scope": "global",
                "rationale": "Smoke-test placeholder: a stateless row-wise transform.",
            },
            {
                "columns": ["num1"],
                "operation": "median_impute",
                "params": {},
                "fit_scope": "per_fold",
                "rationale": "Smoke-test placeholder: a fitted operation, so per-fold.",
            },
        ]
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

# deep_learning_specialist (T-025) shares that schema but carries its own model
# family table (`tabnet`/`node`/`mlp`), so `_MOCK_CLASSICAL_ML_DESIGN`'s
# `"lightgbm"` would be rejected as an unsupported family — it needs its own
# dispatch entry and payload. Mirrors the worked example in
# config/prompts/deep_learning_specialist/v1.md.
_MOCK_DEEP_LEARNING_DESIGN = json.dumps(
    {
        "model_family": "mlp",
        "search_space": {
            "n_layers": {"type": "int", "low": 2, "high": 5},
            "layer_width": {"type": "int", "low": 64, "high": 512, "log": True},
            "learning_rate": {"type": "float", "low": 0.0001, "high": 0.01, "log": True},
        },
        "fixed_params": {"batch_size": 256},
        "preprocessing": ["standard_scaler_fitted_per_fold"],
        "rationale": "Smoke-test placeholder neural experiment design.",
    }
)

# timeseries_specialist (T-027) shares that schema but carries its own model
# family table (`arima`/`prophet`/`exponential_smoothing`/`gradient_boosting_lags`/
# `linear_lags`), so neither sibling payload's family would be accepted — it needs
# its own dispatch entry too. The ARIMA `order` is a hyphenated string token, not a
# JSON array, because `choices` accepts only JSON scalars (see
# config/prompts/timeseries_specialist/v1.md § Tuple-shaped hyperparameters).
_MOCK_TIMESERIES_DESIGN = json.dumps(
    {
        "model_family": "arima",
        "search_space": {
            "order": {"type": "categorical", "choices": ["1-0-0", "1-1-1"]},
            "n_lags": {"type": "int", "low": 1, "high": 14},
        },
        "fixed_params": {},
        "preprocessing": ["differencing"],
        "rationale": "Smoke-test placeholder temporal experiment design.",
    }
)

# error_analyst / hypothesis_generator / experiment_designer (T-032, phase6_evaluation)
# are structured-JSON nodes too — each validates a whitelist-rebuilt schema (see
# src/nodes/llm/_evaluation_llm_common.py and config/prompts/{name}/v1.md), which
# `_MOCK_LLM_CONTENT`'s fenced ```python narrative shape would fail, so all three
# need their own dispatch entries. Every input they read degrades to a placeholder
# on the bare unseeded workspace the smoke test runs them against, so no fixture
# seeding is needed.
_MOCK_ERROR_DIAGNOSIS = json.dumps(
    {
        "root_cause": "underfitting",
        "confidence": 0.4,
        "evidence": ["Smoke-test placeholder evidence."],
        "recommended_focus": "model capacity",
    }
)
# `priority` must be a permutation of 1..N across the list, and
# `addresses_root_cause` one of the five root-cause tokens — a single entry with
# `priority: 1` is the smallest schema-valid payload.
_MOCK_HYPOTHESES = json.dumps(
    {
        "hypotheses": [
            {
                "id": "increase_capacity",
                "statement": "A higher-capacity model will fit the signal.",
                "rationale": "Smoke-test placeholder rationale.",
                "priority": 1,
                "expected_impact": "medium",
                "addresses_root_cause": "underfitting",
            }
        ]
    }
)
# `order` must likewise be a permutation of 1..N, and `target` one of
# `solution_plan`/`feature_spec`/`experiment_design`/`data`.
_MOCK_EXPERIMENT_PLAN = json.dumps(
    {
        "changes": [
            {
                "order": 1,
                "change": "Widen the search space for model capacity.",
                "target": "experiment_design",
                "hypothesis_id": "increase_capacity",
                "expected_effect": "Smoke-test placeholder expected effect.",
            }
        ],
        "rationale": "Smoke-test placeholder experiment plan.",
    }
)

# One payload serves both `analysis_critic` invocations. In
# `config/phases/phase1_understanding.yaml` `data_analyst` is listed in
# `critic.targets`, so the target below is honored as-is; in
# `config/phases/phase4_design.yaml` it is not, so `_parse_verdict`
# (src/nodes/llm/analysis_critic.py:172) normalizes `target_node` to
# `allowed_targets[0]` (`solution_architect`). Either way the `pass` verdict
# is honored and no target node is re-invoked.
_MOCK_ANALYSIS_CRITIC_PASS = json.dumps(
    {
        "verdict": "pass",
        "feedback": "Mocked analysis_critic pass verdict.",
        "target_node": "data_analyst",
    }
)

# `_FAKE_KERNELS` and `_MOCK_COMPETITION_ANALYSIS` are COUPLED:
# `build_index_documents` (src/nodes/llm/_research_common.py) requires the
# extraction indices to cover exactly `1..len(kernels)`, so one fake kernel
# means exactly one extraction carrying `"index": 1`. Editing either alone
# breaks `competition_analyst` in both consuming tests.
_FAKE_KERNELS = [
    {
        "ref": "smoke-user/smoke-kernel",
        "title": "Smoke test kernel",
        "author": "smoke-user",
        "total_votes": 1,
        "url": "https://www.kaggle.com/code/smoke-user/smoke-kernel",
    }
]

# Node name -> mocked response, matched against the `# System prompt — {name}`
# header line. Order is load-bearing: it is preserved exactly from the
# if-chain this table replaced, because `leakage_auditor`'s own prompt prose
# mentions "problem_framer". `analysis_critic` is deliberately absent — it is
# the opt-in branch handled in `_make_llm_side_effect`.
_DISPATCH: tuple[tuple[str, str], ...] = (
    ("problem_framer", _MOCK_PROBLEM_DEFINITION),
    ("leakage_auditor", _MOCK_LEAKAGE_AUDIT),
    ("baseline_designer", _MOCK_BASELINE_DESIGN),
    ("solution_architect", _MOCK_SOLUTION_PLAN),
    ("feature_engineer", _MOCK_FEATURE_SPEC),
    ("classical_ml_specialist", _MOCK_CLASSICAL_ML_DESIGN),
    ("deep_learning_specialist", _MOCK_DEEP_LEARNING_DESIGN),
    ("timeseries_specialist", _MOCK_TIMESERIES_DESIGN),
    ("literature_researcher", _MOCK_EMPTY_EXTRACTION),
    ("web_researcher", _MOCK_EMPTY_EXTRACTION),
    ("competition_analyst", _MOCK_COMPETITION_ANALYSIS),
    ("memory_manager", _MOCK_EMPTY_EXTRACTION),
    # Appended at the end (T-032). Order is load-bearing only for the
    # `problem_framer`/`leakage_auditor` pair above — no later entry's prompt prose
    # mentions an earlier node's name — so appending is safe.
    ("error_analyst", _MOCK_ERROR_DIAGNOSIS),
    ("hypothesis_generator", _MOCK_HYPOTHESES),
    ("experiment_designer", _MOCK_EXPERIMENT_PLAN),
)


def _make_llm_side_effect(
    *, analysis_critic_pass: bool = False
) -> Callable[[list[BaseMessage]], AIMessage]:
    """Build the mocked LLM's `invoke` side effect.

    The returned dispatcher inspects the outgoing SystemMessage's content for
    the calling node's own `# System prompt — {name}` header line (see
    `config/prompts/{name}/v1.md`) — matching the *header* specifically, not a
    bare substring, because leakage_auditor's own prompt prose mentions
    "problem_framer" (its upstream node), which would otherwise mis-route it.
    Falls back to the data_analyst-shaped `_MOCK_LLM_CONTENT` for every other
    node.

    `analysis_critic_pass` is the explicit opt-in for a `pass` verdict:

    - `False` (default) — `analysis_critic` is not dispatched and falls
      through to `_MOCK_LLM_CONTENT`, which `_parse_verdict` normalizes to
      `iterate`. The critic then really re-invokes its target `max_retries`
      times before forcing a pass (CLAUDE.md invariant #5). That is the smoke
      test's pre-existing behavior and it is preserved deliberately: it is
      *incidental* rather than asserted (the smoke test has zero hits for
      `analysis_critic|verdict|forced_pass`), but it is real executed coverage
      of the retry/forced-pass path, so it must not change silently.
    - `True` — return `_MOCK_ANALYSIS_CRITIC_PASS`. The checkpointer test opts
      in because it counts graph-driven node executions, and critic retries
      would otherwise be miscounted as phase re-execution (the original
      `assert 4 == 1`).
    """

    def _llm_side_effect(messages: list[BaseMessage]) -> AIMessage:
        system_content = str(messages[0].content) if messages else ""
        if analysis_critic_pass and "System prompt — analysis_critic" in system_content:
            return AIMessage(content=_MOCK_ANALYSIS_CRITIC_PASS)
        for name, response in _DISPATCH:
            if f"System prompt — {name}" in system_content:
                return AIMessage(content=response)
        return AIMessage(content=_MOCK_LLM_CONTENT)

    return _llm_side_effect


@contextmanager
def graph_llm_mocks(*, analysis_critic_pass: bool = False) -> Iterator[MagicMock]:
    """Patch every real-world-facing call a real node makes when the graph, or
    a phase subgraph, is invoked for real, and yield the mocked LLM.
    Everything else (config/prompt loading, `WorkspaceManager` file writes,
    `code_executor` subprocess execution) runs for real.

    `analysis_critic_pass` is forwarded to `_make_llm_side_effect` — see its
    docstring for why the default is `False`.

    **Enter this before *building* the graph or phase subgraph, not just
    before invoking it.** `LLMNode.__init__` calls `LLMFactory.get()` at
    construction time (`src/nodes/llm/base.py:78`), so a node constructed
    outside this context holds a real provider client and will attempt a live
    connection when invoked, even if the invocation itself happens inside.

    Resetting the `LLMFactory._settings` class-level cache is the **caller's**
    responsibility — this helper deliberately does not touch it. The
    checkpointer fixture resets it on both sides of its `with`; the smoke
    fixture does not. That asymmetry predates B-001 and is left as-is.

    Mock only the LLM network call for every real `LLMNode` a phase might
    resolve to, so consumers never make a real network call or depend on
    provider API keys actually working — mirrors `tests/unit/nodes/llm/
    test_base.py`'s mocking convention, patched at `src.nodes.llm.base`'s
    import location. `Settings.load()` itself (read by `LLMNode.__init__`
    and, for `data_analyst`, by `code_executor.execute`'s real-subprocess
    path) still runs for real against `config/settings.yaml`, which requires
    all five `${...}`-interpolated env vars to be *set* (any non-empty value
    works, matching the fake-key convention in `tests/tools/
    test_code_executor.py` and `tests/unit/llm/test_factory.py`) even though
    the LLM call they'd normally authenticate is mocked away — see
    `set_fake_provider_env` below.

    `literature_researcher`/`web_researcher` (T-017) additionally build their
    own `SearchClient` (arxiv/Semantic Scholar/Tavily, real network) and
    `RagStore` (real Chroma client pointed at `config/settings.yaml`'s
    `workspace.chroma_host`/`chroma_port`, unreachable in this test
    environment) on first use — both are mocked here too so consumers stay
    network-free end to end. With the search clients returning no sources,
    `_MOCK_EMPTY_EXTRACTION` above is the schema-valid response for both nodes
    regardless of the (unused) query text.

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
    the same reason. `hypothesis_generator` (T-032, phase6_evaluation) has the
    same shape again and is patched the same way — with no findings returned,
    its prompt section degrades to the documented
    "No relevant findings found in the RAG store." placeholder.
    """
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = _make_llm_side_effect(analysis_critic_pass=analysis_critic_pass)

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
        patch("src.nodes.llm.hypothesis_generator.RagStore") as mock_hg_rag_store,
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
        mock_hg_rag_store.return_value = MagicMock()
        mock_hg_rag_store.return_value.query.return_value = []
        mock_mlflow.start_run.return_value.__enter__.return_value = MagicMock()
        yield mock_llm


def set_fake_provider_env(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Set the five `${...}`-interpolated env vars `config/settings.yaml`
    requires to be present when `Settings.load()` runs for real.

    `value` is caller-supplied so each consuming test keeps its own existing
    literal (which shows up in failure output and identifies the test).
    """
    for var in (
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GROQ_API_KEY",
        "KAGGLE_USERNAME",
        "KAGGLE_KEY",
    ):
        monkeypatch.setenv(var, value)


def seed_raw_train_csv(workspace_path: Path) -> None:
    """Write the 5-row `data/raw/train.csv` that phase 3's real
    `baseline_runner` subprocess trains on.

    Row count and class balance are matched to a `[0, 1, 2]` / `[3, 4]` fold
    split: both splits contain both classes, which `LogisticRegression.fit`
    requires.

    There are TWO copies of that split and this CSV is coupled to both — change
    one and you must change the others:
      1. `_MOCK_LLM_CONTENT` above, which `validation_strategist` freezes into
         `validation/fold_config.json` (the checkpointer test's path, where
         phase 1 runs for real).
      2. The literal seeded by `_seed_phase3_baseline_fixtures` in
         `tests/integration/phases/test_phase_subgraphs_smoke.py`, which
         exercises phase 3 standalone with no phase-1 run ahead of it and so
         never sees `_MOCK_LLM_CONTENT`'s folds at all.
    """
    raw_dir = workspace_path / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "train.csv").write_text(
        "feature1,target\n1,0\n2,1\n3,0\n4,1\n5,0\n", encoding="utf-8"
    )
