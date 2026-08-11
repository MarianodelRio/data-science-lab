"""solution_architect: reads RAG findings + the Phase 3 baseline results and
designs the modeling strategy for this iteration, writing
design/iteration_{current_iteration}/solution_plan.json.

Overrides `_build_messages` (query the `RagStore` for modeling-strategy
findings and read the baseline results, inject both as an extra
HumanMessage), `_write_output` (extract + validate the JSON payload, write it
via `workspace.write_json`), and `_build_output_state` (set
`state["solution_plan_path"]` — `feature_engineer` and Phase 5's
`specialist_selector`/specialists read this path next).

Scope note (both descopes human-approved for T-021, logged in
context/decisions.md): this v1 does NOT read a "previous error diagnosis"
input — `error_analyst` (T-031, Phase 6) does not exist yet and `LabState`
has no field for its output, so there is nothing to read; and it does NOT
implement "may consult the advisor role" on high-risk decisions — no
existing `LLMNode` in this repo has a precedent for a node dynamically
invoking a second model role mid-execution, and nothing in T-021's "Done
when" checklist tests it. `model_role` is fixed to `reasoning` for the
whole node.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from src.config.settings import Settings
from src.nodes.llm.base import LLMNode, relative_to_workspace
from src.state import LabState
from src.tools.rag import IndexDocument, RagStore
from src.workspace.workspace_manager import WorkspaceManager


def _strip_outer_fence(content: str) -> str:
    """Strip a single outer fence wrapping the entire response, if present.

    Same outer-fence-anchoring approach as `baseline_designer._strip_outer_fence`:
    anchors on the outermost ``` markers only, so an embedded ``` inside a
    string value is never mistaken for the closing fence.
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    if not text.endswith("```") or len(text) < 6:
        raise ValueError("solution_architect response starts with a fence but never closes it")
    first_newline = text.find("\n")
    if first_newline == -1:
        raise ValueError("solution_architect response fence has no content")
    inner = text[first_newline + 1 :]
    closing_idx = inner.rfind("```")
    if closing_idx == -1:
        raise ValueError("solution_architect response fence has no closing delimiter")
    return inner[:closing_idx].strip()


def _extract_json(content: str) -> dict[str, Any]:
    """Extract a JSON object from the LLM response.

    Accepts: raw JSON with no fence, or the entire response wrapped in a
    single ```json or unlabeled ``` fence. Invalid JSON raises a clear
    ValueError naming 'solution_architect'.
    """
    text = _strip_outer_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"solution_architect response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"solution_architect response must be a JSON object, got {type(data).__name__}"
        )
    return data


def _validate_solution_plan(data: dict[str, Any]) -> dict[str, Any]:
    model_families = data.get("model_families")
    if (
        not isinstance(model_families, list)
        or not model_families
        or not all(isinstance(m, str) and m.strip() for m in model_families)
    ):
        raise ValueError(
            "solution_architect response missing required non-empty list[str] field "
            "'model_families'"
        )
    if len(set(model_families)) != len(model_families):
        raise ValueError(
            "solution_architect response 'model_families' contains duplicate entries: "
            f"{model_families!r}"
        )

    order = data.get("order")
    if (
        not isinstance(order, list)
        or not order
        or not all(isinstance(o, str) and o.strip() for o in order)
    ):
        raise ValueError(
            "solution_architect response missing required non-empty list[str] field 'order'"
        )
    if len(set(order)) != len(order):
        raise ValueError(
            f"solution_architect response 'order' contains duplicate entries: {order!r}"
        )
    if sorted(order) != sorted(model_families):
        raise ValueError(
            f"solution_architect response 'order' {order!r} must contain exactly the same "
            f"entries as 'model_families' {model_families!r}"
        )

    ensembling_strategy = data.get("ensembling_strategy")
    if not isinstance(ensembling_strategy, str) or not ensembling_strategy.strip():
        raise ValueError(
            "solution_architect response missing required non-empty string field "
            "'ensembling_strategy'"
        )

    realistic_ceiling = data.get("realistic_ceiling")
    if not isinstance(realistic_ceiling, dict):
        raise ValueError(
            "solution_architect response missing required dict field 'realistic_ceiling', "
            f"got {realistic_ceiling!r}"
        )
    metric = realistic_ceiling.get("metric")
    if not isinstance(metric, str) or not metric.strip():
        raise ValueError(
            "solution_architect response 'realistic_ceiling.metric' must be a non-empty string"
        )
    target_score = realistic_ceiling.get("target_score")
    if isinstance(target_score, bool) or not isinstance(target_score, (int, float)):
        raise ValueError(
            "solution_architect response 'realistic_ceiling.target_score' must be a number, "
            f"got {target_score!r}"
        )
    ceiling_rationale = realistic_ceiling.get("rationale")
    if not isinstance(ceiling_rationale, str) or not ceiling_rationale.strip():
        raise ValueError(
            "solution_architect response 'realistic_ceiling.rationale' must be a non-empty string"
        )

    rationale = data.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError(
            "solution_architect response missing required non-empty string field 'rationale'"
        )

    return {
        "model_families": model_families,
        "order": order,
        "ensembling_strategy": ensembling_strategy,
        "realistic_ceiling": {
            "metric": metric,
            "target_score": float(target_score),
            "rationale": ceiling_rationale,
        },
        "rationale": rationale,
    }


def _format_rag_findings(documents: list[IndexDocument]) -> str:
    """Render retrieved `IndexDocument`s as numbered findings, or a fixed
    placeholder string when the RAG store returned nothing — mirrors
    `baseline_designer`'s not-yet-available degradation convention for
    upstream context that may legitimately be empty."""
    if not documents:
        return "No relevant findings found in the RAG store."

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


def _read_baseline_results(state: LabState, workspace: WorkspaceManager) -> str:
    """Same not-yet-available/unreadable degradation as
    `baseline_designer._read_problem_definition`, for
    `state['baseline_results_path']`."""
    path = state.get("baseline_results_path") or ""
    if not path:
        return "(baseline results not yet available)"
    try:
        data = workspace.read_json(relative_to_workspace(path, workspace))
    except OSError:
        return f"(unable to read baseline results at {path})"
    return json.dumps(data, indent=2)


class SolutionArchitectNode(LLMNode):
    name = "solution_architect"

    def __init__(
        self,
        *,
        rag_store: RagStore | None = None,
        agent_config_dir: str | Path | None = None,
        prompts_dir: str | Path | None = None,
    ) -> None:
        super().__init__(agent_config_dir=agent_config_dir, prompts_dir=prompts_dir)
        self._rag_store = rag_store  # injectable for tests, same convention as WebResearcherNode

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

        query = f"modeling strategy for {state['competition_name']}"
        sources = self._ensure_rag_store(state["competition_name"]).query(query)
        rag_section = _format_rag_findings(sources)

        baseline_section = _read_baseline_results(state, workspace)

        messages.append(
            HumanMessage(
                content=(
                    f"## RAG findings\n\n{rag_section}\n\n## Baseline results\n\n{baseline_section}"
                )
            )
        )
        return messages

    def _write_output(
        self, workspace: WorkspaceManager, relative_path: str, response: BaseMessage
    ) -> str:
        content = response.content if isinstance(response.content, str) else str(response.content)
        data = _extract_json(content)
        validated = _validate_solution_plan(data)
        return workspace.write_json(relative_path, validated)

    def _build_output_state(self, written_path: str, state: LabState) -> dict[str, Any]:
        return {"solution_plan_path": written_path}
