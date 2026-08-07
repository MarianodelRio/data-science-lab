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
# literature_researcher/web_researcher (T-017) are also structured-JSON nodes
# (a JSON array, one entry per searched source) — paired below with the
# search-client mocks in `_mock_llm`, which make both nodes see zero sources,
# so an empty array is always the correct, schema-valid extraction response.
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
    if "System prompt — literature_researcher" in system_content:
        return AIMessage(content=_MOCK_EMPTY_EXTRACTION)
    if "System prompt — web_researcher" in system_content:
        return AIMessage(content=_MOCK_EMPTY_EXTRACTION)
    if "System prompt — competition_analyst" in system_content:
        return AIMessage(content=_MOCK_COMPETITION_ANALYSIS)
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
    ):
        mock_factory.get.return_value = mock_llm
        mock_lit_client.return_value.search.return_value = []
        mock_web_client.return_value.search.return_value = []
        mock_lit_rag_store.return_value = MagicMock()
        mock_web_rag_store.return_value = MagicMock()
        mock_rag_store_cls.return_value = MagicMock()
        yield


@pytest.mark.parametrize("stem", PHASE_ORDER)
def test_phase_subgraph_compiles_and_runs(stem: str, tmp_path) -> None:
    module = importlib.import_module(f"src.graph.phases.{stem}")
    compiled = module.build(resolve_node)

    state = new_state("comp", str(tmp_path))
    result = compiled.invoke(state)

    assert result["competition_name"] == "comp"


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
