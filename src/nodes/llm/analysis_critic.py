"""analysis_critic: reviews the analytical outputs produced during Pipeline
Phase 1 (Understanding) and Phase 4 (Design) against a methodological-rigor
rubric, and returns a `pass`/`iterate` verdict — re-invoking the named target
node directly for up to `max_retries` iterate cycles before forcing a pass.

Overrides `LLMNode.__call__` wholesale (rather than composing via the
`_build_messages`/`_write_output`/`_build_output_state` hooks) because it owns
its own retry control flow internally, re-invoking target nodes directly
through `src.graph.node_resolver.resolve_node` — see context/decisions.md's
2026-08-05 T-009 entry ("a critic's own node function re-invokes its target
node(s) directly... entirely inside its own `__call__`"). `LLMNode.__init__`
is inherited unchanged (loads `AgentConfig`, prompt, LLM via `LLMFactory`,
`_max_messages_per_node`).

Because this custom `__call__` instantiates `WorkspaceManager` directly in
this module (not via the base class's own `__call__`), unit tests must patch
`src.nodes.llm.analysis_critic.WorkspaceManager` and
`src.nodes.llm.analysis_critic.resolve_node` — not `src.nodes.llm.base.*` for
those two (though `src.nodes.llm.base.LLMFactory`/`Settings` are still
correct, since `__init__` is inherited).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.config.loaders import load_phase_config
from src.graph.node_resolver import resolve_node
from src.nodes.llm.base import LLMNode, trim_context
from src.nodes.llm.errors import FoldsAlreadyFrozenError
from src.state import LabState
from src.workspace.workspace_manager import WorkspaceManager

# Which `LabState` field holds a given target node's output path. Verified
# against `src/state.py` (the protected LabState contract) rather than
# assumed.
_TARGET_STATE_FIELDS: dict[str, str] = {
    "data_analyst": "eda_report_path",
    "problem_framer": "problem_definition_path",
    "validation_strategist": "validation_config_path",
    "solution_architect": "solution_plan_path",
    "feature_engineer": "feature_spec_path",
}

# Targets whose output lives at a fixed workspace-relative path rather than a
# `LabState` field. `leakage_auditor` writes `reports/leakage_audit.json` but
# does not own a `LabState` path field (see leakage_auditor.py's module
# docstring — its delta is `{"messages": [...]}` only).
_TARGET_FIXED_PATHS: dict[str, str] = {
    "leakage_auditor": "reports/leakage_audit.json",
}

_VALID_VERDICTS = ("pass", "iterate")


def _relative_to_workspace(path: str, workspace: WorkspaceManager) -> str:
    """`WorkspaceManager.write_text`/`write_json` return an *absolute* path,
    and `LabState` path fields store that value verbatim. But `read_text`
    requires a *relative* path and rejects absolute ones. Re-relativize
    against the current workspace root before reading — already-relative
    input (e.g. in unit tests, or the fixed leakage-audit path) passes
    through unchanged. Pattern copied from problem_framer.py/leakage_auditor.py.
    """
    p = Path(path)
    if not p.is_absolute():
        return path
    return str(p.relative_to(workspace.workspace_path))


def _read_target_content(target: str, state: dict[str, Any], workspace: WorkspaceManager) -> str:
    """Read one target's output content as raw text (never `read_json` — the
    review only needs the text, and targets vary in output shape). Missing
    upstream paths and unreadable files degrade to a placeholder string
    rather than raising, so a partially-completed phase can still be
    reviewed (and is treated by the prompt as a hard iterate signal)."""
    if target in _TARGET_FIXED_PATHS:
        raw_path = _TARGET_FIXED_PATHS[target]
    elif target in _TARGET_STATE_FIELDS:
        field = _TARGET_STATE_FIELDS[target]
        raw_path = str(state.get(field) or "")
        if not raw_path:
            return f"({target}'s output not yet available)"
    else:
        raise ValueError(f"analysis_critic has no known output-path mapping for target '{target}'")

    relative_path = _relative_to_workspace(raw_path, workspace)
    try:
        return workspace.read_text(relative_path)
    except OSError:
        return f"(unable to read {target}'s output at {relative_path})"


def _build_targets_message(
    targets: tuple[str, ...], state: dict[str, Any], workspace: WorkspaceManager
) -> str:
    sections = [
        f"## {target}\n\n{_read_target_content(target, state, workspace)}" for target in targets
    ]
    return "\n\n".join(sections)


def _strip_outer_fence(content: str) -> str | None:
    """Strip a single outer fence wrapping the entire response, if present.

    Unlike sibling nodes' `_strip_outer_fence` (problem_framer.py,
    leakage_auditor.py), this never raises: an unclosed/malformed fence
    returns `None` so the caller can normalize to an `iterate` verdict
    instead of propagating an exception (§2.5 of the T-016 plan — malformed
    LLM output must never crash the critic).
    """
    text = content.strip()
    if not text.startswith("```"):
        return text
    if not text.endswith("```") or len(text) < 6:
        return None
    first_newline = text.find("\n")
    if first_newline == -1:
        return None
    inner = text[first_newline + 1 :]
    closing_idx = inner.rfind("```")
    if closing_idx == -1:
        return None
    return inner[:closing_idx].strip()


def _parse_verdict(content: str, allowed_targets: tuple[str, ...]) -> tuple[str, str, str]:
    """Parse the LLM's verdict JSON. Never raises — always returns a
    normalized `(verdict, feedback, target_node)` triple:

    - `verdict` is always exactly `"pass"` or `"iterate"` (malformed JSON,
      a non-object payload, or any value other than `"pass"`/`"iterate"`
      normalizes to `"iterate"`).
    - `target_node` is always exactly one of `allowed_targets` (falls back
      to `allowed_targets[0]` when omitted/unrecognized).
    - `feedback` is never empty (a default message is synthesized, worded
      differently depending on whether the verdict ended up pass, iterate,
      or the response failed to parse at all).
    """
    fallback_target = allowed_targets[0] if allowed_targets else ""

    stripped = _strip_outer_fence(content)
    data: dict[str, Any] | None = None
    if stripped is not None:
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            data = parsed

    if data is None:
        return (
            "iterate",
            "analysis_critic: could not parse a JSON verdict object from the LLM response "
            "(parse failure); treating this as a hard iterate signal.",
            fallback_target,
        )

    raw_verdict = data.get("verdict")
    verdict = raw_verdict if raw_verdict in _VALID_VERDICTS else "iterate"

    raw_target = data.get("target_node")
    target_node = raw_target if raw_target in allowed_targets else fallback_target

    feedback = data.get("feedback")
    if not isinstance(feedback, str) or not feedback.strip():
        feedback = (
            "analysis_critic: pass verdict with no specific feedback provided."
            if verdict == "pass"
            else "analysis_critic: iterate verdict with no specific feedback provided."
        )

    return verdict, feedback, target_node


class AnalysisCriticNode(LLMNode):
    name = "analysis_critic"

    def __call__(self, state: LabState) -> dict[str, Any]:
        workspace = WorkspaceManager(state["workspace_path"])

        is_phase4 = bool(state.get("feature_spec_path"))
        phase_stem = "phase4_design" if is_phase4 else "phase1_understanding"
        phase_config = load_phase_config(phase_stem)
        critic_config = phase_config.critic
        if critic_config is None:
            raise ValueError(f"analysis_critic invoked but {phase_stem}.yaml has no 'critic' block")

        max_retries = critic_config.max_retries
        allowed_targets = critic_config.targets
        fallback_target = allowed_targets[0] if allowed_targets else ""

        # Global safety cap: guards CLAUDE.md invariant #5 ("no infinite
        # loops") against a pathological LLM that names a *different* target
        # every cycle, which would never trip the per-target retry counter
        # alone. Deliberate addition beyond the literal task text.
        max_total_cycles = (max_retries + 1) * max(len(allowed_targets), 1)

        retry_counts: dict[str, int] = {}
        working_state: dict[str, Any] = dict(state)
        accumulated_messages: list[BaseMessage] = []
        attempts: list[dict[str, Any]] = []

        for _cycle in range(max_total_cycles):
            trimmed = trim_context(working_state.get("messages", []), self._max_messages_per_node)
            human_message = HumanMessage(
                content=_build_targets_message(allowed_targets, working_state, workspace)
            )
            messages: list[BaseMessage] = [
                SystemMessage(content=self.system_prompt),
                *trimmed,
                human_message,
            ]

            response = self.llm.invoke(messages)
            accumulated_messages.append(response)
            working_state["messages"] = [*working_state.get("messages", []), response]

            content = (
                response.content if isinstance(response.content, str) else str(response.content)
            )
            verdict, feedback, target_node = _parse_verdict(content, allowed_targets)

            if verdict == "pass":
                attempts.append(
                    {"verdict": "pass", "feedback": feedback, "target_node": target_node}
                )
                break

            # verdict == "iterate"
            count = retry_counts.get(target_node, 0)
            if count >= max_retries:
                attempts.append(
                    {
                        "verdict": "pass",
                        "feedback": (
                            f"analysis_critic: retry budget exhausted for '{target_node}' after "
                            f"{max_retries} iterate cycle(s); forcing pass. "
                            f"Last feedback: {feedback}"
                        ),
                        "target_node": target_node,
                        "forced_pass": True,
                    }
                )
                break

            retry_counts[target_node] = count + 1
            try:
                delta = resolve_node(target_node)(cast(LabState, working_state))
            except FoldsAlreadyFrozenError:
                attempts.append(
                    {
                        "verdict": "pass",
                        "feedback": (
                            f"analysis_critic: cannot retry '{target_node}' — its output is "
                            "already frozen (FoldsAlreadyFrozenError); forcing pass."
                        ),
                        "target_node": target_node,
                        "forced_pass": True,
                        "folds_frozen": True,
                    }
                )
                break

            attempts.append(
                {"verdict": "iterate", "feedback": feedback, "target_node": target_node}
            )

            target_delta = dict(delta) if isinstance(delta, dict) else {}
            target_messages = target_delta.pop("messages", None)
            working_state.update(target_delta)
            if target_messages:
                accumulated_messages.extend(target_messages)
                working_state["messages"] = [*working_state.get("messages", []), *target_messages]
        else:
            attempts.append(
                {
                    "verdict": "pass",
                    "feedback": (
                        "analysis_critic: global safety cap of "
                        f"{max_total_cycles} review cycle(s) reached without a pass verdict; "
                        "forcing pass."
                    ),
                    "target_node": fallback_target,
                    "forced_pass": True,
                }
            )

        relative_path = self._resolve_output_path(state)
        record = {
            "phase": phase_stem,
            "targets": list(allowed_targets),
            "attempts": attempts,
            "final_verdict": attempts[-1],
        }
        workspace.write_json(relative_path, record)

        return {"messages": accumulated_messages}
