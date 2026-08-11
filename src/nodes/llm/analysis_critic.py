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
from typing import Any, cast

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from src.config.loaders import load_phase_config
from src.graph.node_resolver import resolve_node
from src.nodes.llm.base import LLMNode, relative_to_workspace, trim_context
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

    relative_path = relative_to_workspace(raw_path, workspace)
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


def _fence_candidates(content: str) -> list[str]:
    """Return, in preference order, the un-fenced strings worth attempting
    to `json.loads`.

    A single anchor is not safe in general: always taking the LAST '```' as
    the closing delimiter (mirroring the sibling nodes' `_strip_outer_fence`
    fix for embedded ``` runs *inside* a JSON string value, e.g. a feedback
    message that quotes a fenced code snippet) is correct there — the true
    close is textually last — but wrong if the LLM response accidentally
    contains more than one top-level fenced block (e.g. stray narrative
    fenced separately from the intended JSON one, despite the prompt's "no
    other prose" instruction): there, the LAST '```' belongs to the
    *unwanted* trailing block, and anchoring on it swallows/truncates the
    real JSON.

    Rather than commit to one anchor, this returns multiple candidates —
    the raw content unmodified, a last-'```'-anchored strip, and a
    first-'```'-anchored strip — and `_extract_verdict_object` keeps
    whichever one actually parses as a JSON object. At most three cheap
    `json.loads` attempts; malformed JSON is always normalized to a safe
    `iterate` verdict rather than raising, so trying more than one anchor
    carries no risk.
    """
    text = content.strip()
    candidates = [text]
    if text.startswith("```") and text.endswith("```") and len(text) >= 6:
        first_newline = text.find("\n")
        if first_newline != -1:
            inner = text[first_newline + 1 :]
            last_close = inner.rfind("```")
            if last_close != -1:
                candidates.append(inner[:last_close].strip())
            first_close = inner.find("```")
            if first_close != -1 and first_close != last_close:
                candidates.append(inner[:first_close].strip())
    return candidates


def _extract_verdict_object(content: str) -> dict[str, Any] | None:
    """Try each fence-stripping candidate from `_fence_candidates` in turn;
    return the first one that parses as a JSON object, or `None` if none
    do. Never raises."""
    for candidate in _fence_candidates(content):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


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

    data = _extract_verdict_object(content)

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


def _detect_phase_stem(state: LabState) -> str:
    """Detect whether this invocation is reviewing Phase 1 (Understanding)
    or Phase 4 (Design), returning the matching `config/phases/*.yaml`
    filename stem.

    Deliberately does NOT read `state["phase"]`: that field is only stamped
    by the graph *after* a phase subgraph finishes (see
    context/decisions.md's 2026-08-06 `[Orchestrator, /explore]` entry on
    checkpoint semantics — the phase-completion write is what feeds the
    forward-only checkpoint), so while `analysis_critic` itself is running
    — as the *last* node inside the very phase it is reviewing —
    `state["phase"]` still reflects the *previous* completed phase, not the
    current one. Reading it here would misdetect every single invocation.
    `feature_spec_path` is only ever written by `feature_engineer` (a
    Phase 4-only node), so its presence is a reliable proxy instead.
    """
    return "phase4_design" if bool(state.get("feature_spec_path")) else "phase1_understanding"


class AnalysisCriticNode(LLMNode):
    name = "analysis_critic"

    def _resolve_output_path(self, state: LabState) -> str:
        """Override the base implementation to also interpolate `{phase}`.

        `LLMNode._resolve_output_path` (src/nodes/llm/base.py) only
        substitutes `{iteration}`. But `analysis_critic` runs twice per
        competition run — once at the end of Phase 1, once at the end of
        Phase 4 — and nothing increments `current_iteration` between them,
        so both invocations see `current_iteration == 0`. An
        `{iteration}`-only pattern would make Phase 4's write silently
        clobber Phase 1's verdict record. `output_file_pattern`
        (config/agents/analysis_critic.yaml) therefore also contains
        `{phase}`, which the base class doesn't know how to fill — hence
        this local reimplementation rather than calling `super()`.
        """
        phase_stem = _detect_phase_stem(state)
        try:
            return self.config.output_file_pattern.format(
                iteration=state["current_iteration"], phase=phase_stem
            )
        except KeyError as e:
            raise ValueError(
                f"output_file_pattern {self.config.output_file_pattern!r} for agent "
                f"'{self.name}' has an unresolved placeholder {e}"
            ) from e

    def __call__(self, state: LabState) -> dict[str, Any]:
        workspace = WorkspaceManager(state["workspace_path"])

        phase_stem = _detect_phase_stem(state)
        phase_config = load_phase_config(phase_stem)
        critic_config = phase_config.critic
        if critic_config is None:
            raise ValueError(f"analysis_critic invoked but {phase_stem}.yaml has no 'critic' block")

        max_retries = critic_config.max_retries
        allowed_targets = critic_config.targets
        fallback_target = allowed_targets[0] if allowed_targets else ""

        # Global safety cap: bounds the loop even under a pathological LLM
        # response pattern, guarding CLAUDE.md invariant #5 ("no infinite
        # loops"). Note on reachability: because `target_node` is always
        # normalized to one of the finite `allowed_targets` (see
        # `_parse_verdict`), a pigeonhole argument shows the per-target
        # `count >= max_retries` guard alone already bounds *any*
        # round-robin-style target pattern within this many cycles — with N
        # targets, N * (max_retries + 1) cycles cannot be filled without
        # some target reaching `max_retries + 1` visits (and thus tripping
        # its own guard) first. So under the current formula and the
        # target-normalization guarantee, the `for...else` branch below is
        # unreachable via any input the LLM can currently produce; it is
        # kept as defense-in-depth against a future change (e.g. a bug that
        # lets `target_node` escape `allowed_targets`, or a different cap
        # formula) rather than against a presently-realizable adversarial
        # input.
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
                # Scoped to `validation_strategist` specifically: today it's
                # the only node that ever raises this exception (its
                # write-once guard on `validation/fold_config.json`, per
                # CLAUDE.md invariant #1), but the exception class itself
                # isn't inherently tied to any single target. Re-raise for
                # any other target rather than silently mislabeling the
                # attempt record with `"folds_frozen": True` — a claim
                # specifically about CV fold freezing — for an unrelated
                # write-once violation a future node might reuse this same
                # exception class for.
                if target_node != "validation_strategist":
                    raise
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
            # Only the local `working_state` picks up non-"messages" keys
            # from the retried target's delta (never this node's own
            # returned delta — see the messages-only contract at the bottom
            # of __call__). This assumes a retried target's own output path
            # is a pure function of (workspace, current_iteration) — true
            # for every currently-implemented target (each writes to a
            # fixed or iteration-keyed path and returns that same path
            # again on a second call within one cycle) — so re-reading its
            # content on the next review cycle via `_read_target_content`
            # naturally picks up the retried output without needing to
            # propagate the updated path field back out of this node.
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
