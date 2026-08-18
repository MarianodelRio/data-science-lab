"""hypothesis_generator: reads `error_analyst`'s diagnosis, queries this
competition's RAG store for what has already been tried, and writes a
prioritized hypothesis set to `reports/hypotheses_{current_iteration}.json`.

Fourth node of `config/phases/phase6_evaluation.yaml`'s sequence,
`model_role: reasoning`, no critic in this phase.

## RAG access

The `RagStore` is an optional keyword-only constructor argument plus a lazy
`_ensure_rag_store` built from `Settings.load().workspace.chroma_host/port` —
byte-for-byte the convention `solution_architect`, `memory_manager` and
`web_researcher` already use. That preserves zero-argument construction for
`src/graph/node_resolver.py`'s `cls()` (which never passes arguments) while
giving tests a direct injection point, and it never opens a Chroma connection
at import time.

The query names the diagnosed root cause when one was read and always names the
competition, so the retrieved findings are the prior attempts *for this
competition* rather than generic ones.

## Stashing across `_build_messages` -> `_write_output`

The artifact records `rag_query` and `prior_attempts_considered` (the number of
documents the query returned), but `LLMNode._write_output` receives neither the
state nor the retrieved documents. `LLMNode.__call__` runs `_build_messages`
before `_write_output` within the same call and Phase 6 is strictly sequential
(`parallel_nodes: []`), so `_build_messages` stashes both on `self`. They are
initialized in `__init__` too, so a direct `_write_output` call can never hit an
`AttributeError`. This coupling is the reason those two attributes exist; do not
reorder the base class's calls without revisiting it.

## Degradation

A missing or unreadable `reports/error_diagnosis_{N}.json` degrades to a
placeholder section and a generic RAG query — it never raises. See
`_evaluation_llm_common`'s module docstring for why the artifact number this
node reads at can legitimately diverge from the one `score_evaluator` wrote.

## No `LabState` field

`_build_output_state` is deliberately not overridden (returns `{}` beyond
`messages`): `src/state.py` is a protected contract, and the consumer of this
artifact is `experiment_designer`, which reads the workspace file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.config.settings import Settings
from src.nodes.llm import _evaluation_llm_common as common
from src.nodes.llm.base import LLMNode
from src.state import LabState
from src.tools.rag import IndexDocument, RagStore
from src.workspace.workspace_manager import WorkspaceManager

NODE_NAME = "hypothesis_generator"

EXPECTED_IMPACTS: tuple[str, ...] = ("high", "medium", "low")

_DIAGNOSIS_MISSING = "(error diagnosis not yet available)"
_NO_RAG_FINDINGS = "No relevant findings found in the RAG store."

_MAX_HYPOTHESES = 5

# The exact per-hypothesis key set, in written order. `_validate_hypotheses`
# rebuilds a fresh dict with exactly these keys — the LLM's own object is never
# written through.
HYPOTHESIS_KEYS = (
    "id",
    "statement",
    "rationale",
    "priority",
    "expected_impact",
    "addresses_root_cause",
)


def _format_rag_findings(documents: list[IndexDocument]) -> str:
    """Render retrieved `IndexDocument`s as numbered findings, or a fixed
    placeholder when the store returned nothing — mirrors
    `solution_architect._format_rag_findings`. Kept node-local deliberately: it
    is a single-consumer formatter, not part of the extraction trio the
    `base.py` hoist tracks."""
    if not documents:
        return _NO_RAG_FINDINGS

    blocks = []
    for i, doc in enumerate(documents, start=1):
        blocks.append(
            f"### Finding {i}\n\n"
            f"- source: {doc.source}\n"
            f"- key_findings: {doc.key_findings}\n"
            f"- methods_used: {doc.methods_used}\n"
            f"- problem_type: {doc.problem_type}\n"
            f"- dataset_characteristics: {doc.dataset_characteristics}\n"
            f"- relevance_score: {doc.relevance_score}"
        )
    return "\n\n".join(blocks)


def _build_rag_query(competition_name: str, root_cause: str | None) -> str:
    """The competition name is always present so retrieval stays scoped to this
    competition's own prior attempts; the root cause narrows it when a diagnosis
    was actually read."""
    if root_cause:
        return f"{root_cause} remedies and previously tried approaches for {competition_name}"
    return f"previously tried approaches and failures for {competition_name}"


def _root_cause_of(diagnosis: dict[str, Any] | None) -> str | None:
    """The diagnosed root cause, or `None` when the diagnosis was missing or
    carries a token outside the pinned vocabulary (an out-of-vocabulary value
    must not be interpolated into a retrieval query)."""
    if diagnosis is None:
        return None
    root_cause = diagnosis.get("root_cause")
    if isinstance(root_cause, str) and root_cause in common.ROOT_CAUSES:
        return root_cause
    return None


def _validate_hypothesis(entry: dict[str, Any], index: int) -> dict[str, Any]:
    field = f"hypotheses[{index}]"
    return {
        "id": common.validate_non_empty_str(entry.get("id"), f"{field}.id", NODE_NAME),
        "statement": common.validate_non_empty_str(
            entry.get("statement"), f"{field}.statement", NODE_NAME
        ),
        "rationale": common.validate_non_empty_str(
            entry.get("rationale"), f"{field}.rationale", NODE_NAME
        ),
        "priority": common.validate_int(entry.get("priority"), f"{field}.priority", NODE_NAME),
        "expected_impact": common.validate_enum(
            entry.get("expected_impact"), f"{field}.expected_impact", EXPECTED_IMPACTS, NODE_NAME
        ),
        "addresses_root_cause": common.validate_enum(
            entry.get("addresses_root_cause"),
            f"{field}.addresses_root_cause",
            common.ROOT_CAUSES,
            NODE_NAME,
        ),
    }


def _validate_hypotheses(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Whitelist rebuild of the hypothesis list, sorted ascending by `priority`.

    Sorting here rather than trusting the response order is what makes
    "prioritized" an assertable property of the artifact: `experiment_designer`
    reads the list top-down.
    """
    raw = common.validate_object_list(
        data.get("hypotheses"), "hypotheses", NODE_NAME, min_len=1, max_len=_MAX_HYPOTHESES
    )
    hypotheses = [_validate_hypothesis(entry, i) for i, entry in enumerate(raw)]

    ids = [h["id"].strip().lower() for h in hypotheses]
    if len(set(ids)) != len(ids):
        raise ValueError(
            f"{NODE_NAME} response field 'hypotheses' contains duplicate ids "
            f"(compared case-insensitively): {[h['id'] for h in hypotheses]!r}"
        )
    common.validate_rank_permutation([h["priority"] for h in hypotheses], "priority", NODE_NAME)

    return sorted(hypotheses, key=lambda h: h["priority"])


class HypothesisGeneratorNode(LLMNode):
    name = NODE_NAME

    def __init__(
        self,
        *,
        rag_store: RagStore | None = None,
        agent_config_dir: str | Path | None = None,
        prompts_dir: str | Path | None = None,
    ) -> None:
        super().__init__(agent_config_dir=agent_config_dir, prompts_dir=prompts_dir)
        self._rag_store = rag_store  # injectable for tests, same convention as SolutionArchitect
        self._iteration: int = 0
        self._rag_query: str = ""
        self._prior_attempts_considered: int = 0

    def _ensure_rag_store(self, competition_name: str) -> RagStore:
        if self._rag_store is None:
            settings = Settings.load()
            self._rag_store = RagStore(
                competition_name,
                settings.workspace.chroma_host,
                settings.workspace.chroma_port,
            )
        return self._rag_store

    def _build_messages(
        self, trimmed_messages: list[BaseMessage], state: LabState
    ) -> list[BaseMessage]:
        messages = super()._build_messages(trimmed_messages, state)
        workspace = WorkspaceManager(state["workspace_path"])
        iteration = common.current_iteration(state)

        diagnosis_path = common.ERROR_DIAGNOSIS_PATTERN.format(iteration=iteration)
        diagnosis = common.read_workspace_json(diagnosis_path, workspace)

        competition_name = state["competition_name"]
        query = _build_rag_query(competition_name, _root_cause_of(diagnosis))
        documents = self._ensure_rag_store(competition_name).query(query)

        self._iteration = iteration
        self._rag_query = query
        self._prior_attempts_considered = len(documents)

        messages.append(
            HumanMessage(
                content=(
                    "## Error diagnosis\n\n"
                    f"{common.render_json_section(diagnosis, _DIAGNOSIS_MISSING)}\n\n"
                    "## Prior attempts (RAG)\n\n"
                    f"{_format_rag_findings(documents)}"
                )
            )
        )
        return messages

    def _resolve_output_path(self, state: LabState) -> str:
        """Same coerced iteration the artifact's own `iteration` field records,
        so the filename number and the recorded number can never disagree.
        `LLMNode`'s default reads `state["current_iteration"]` raw, which would
        file a boolean as `..._True.json` next to an `"iteration": 0` body."""
        return self.config.output_file_pattern.format(iteration=common.current_iteration(state))

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        hypotheses = _validate_hypotheses(common.extract_json_object(content, NODE_NAME))
        artifact = {
            "iteration": self._iteration,
            "hypotheses": hypotheses,
            "rag_query": self._rag_query,
            "prior_attempts_considered": self._prior_attempts_considered,
        }
        return workspace.write_json(relative_path, artifact)
