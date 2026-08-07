"""Unit tests for src/nodes/llm/analysis_critic.py.

`analysis_critic` overrides `LLMNode.__call__` wholesale and instantiates
`WorkspaceManager` directly inside its own module (not via the base class), so
unlike the other sibling node tests, `WorkspaceManager` and `resolve_node` are
patched at `src.nodes.llm.analysis_critic`, not `src.nodes.llm.base`.
`LLMFactory`/`Settings` are still patched at `src.nodes.llm.base` since
`LLMNode.__init__` is inherited unchanged. No network calls, no real
filesystem writes, no real node re-invocation (`resolve_node` is always
mocked).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.config.loaders import load_agent_config, load_phase_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings
from src.nodes.llm.analysis_critic import AnalysisCriticNode
from src.nodes.llm.errors import FoldsAlreadyFrozenError
from src.state import new_state

PHASE1_TARGETS = ("data_analyst", "problem_framer", "validation_strategist", "leakage_auditor")
PHASE4_TARGETS = ("solution_architect", "feature_engineer")


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


def _verdict(verdict: str, feedback: str, target_node: str) -> str:
    return json.dumps({"verdict": verdict, "feedback": feedback, "target_node": target_node})


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok", "data_analyst"))
    return llm


@pytest.fixture
def patched_llm_factory(mock_llm: MagicMock):
    with patch("src.nodes.llm.base.LLMFactory") as mock_factory:
        mock_factory.get.return_value = mock_llm
        yield mock_factory


@pytest.fixture
def patched_settings():
    with patch("src.nodes.llm.base.Settings") as mock_settings_cls:
        mock_settings_cls.load.return_value = _make_settings()
        yield mock_settings_cls


@pytest.fixture
def mock_workspace_manager():
    """Patched at `src.nodes.llm.analysis_critic` — the node's custom
    `__call__` constructs `WorkspaceManager` directly in that module, never
    going through the base class's own `__call__`."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_text.return_value = "(placeholder content)"
    instance.write_json.return_value = (
        "/workspace/reports/critic_verdicts_phase1_understanding_iter0.json"
    )
    with patch("src.nodes.llm.analysis_critic.WorkspaceManager") as mock_wm_cls:
        mock_wm_cls.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    return state


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("analysis_critic")

    assert config.name == "analysis_critic"
    assert config.model_role == "fast"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "reports/critic_verdicts_{phase}_iter{iteration}.json"
    assert config.max_tokens == 2048

    prompt = PromptLoader().load("analysis_critic", "v1")
    assert prompt.strip() != ""
    assert "verdict" in prompt


# -- phase identity detection --


def test_phase1_selected_when_feature_spec_path_empty(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok", "data_analyst"))

    with patch("src.nodes.llm.analysis_critic.resolve_node") as mock_resolve:
        node = AnalysisCriticNode()
        state = _build_state()
        assert state["feature_spec_path"] == ""

        node(state)

        mock_resolve.assert_not_called()

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    record = args[1]
    assert record["phase"] == "phase1_understanding"
    assert record["targets"] == list(PHASE1_TARGETS)


def test_phase4_selected_when_feature_spec_path_set(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok", "solution_architect"))

    with patch("src.nodes.llm.analysis_critic.resolve_node"):
        node = AnalysisCriticNode()
        state = _build_state()
        state["feature_spec_path"] = "reports/feature_spec.json"

        node(state)

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    record = args[1]
    assert record["phase"] == "phase4_design"
    assert record["targets"] == list(PHASE4_TARGETS)


# -- verdict / feedback / retry mechanics (Done-when critical) --


def test_iterate_verdict_yields_nonempty_feedback_and_target_node(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.side_effect = [
        AIMessage(
            content=_verdict(
                "iterate",
                "framing is inconsistent with the EDA's target distribution",
                "problem_framer",
            )
        ),
        AIMessage(content=_verdict("pass", "looks good now", "problem_framer")),
    ]

    with patch(
        "src.nodes.llm.analysis_critic.resolve_node", return_value=MagicMock(return_value={})
    ):
        node = AnalysisCriticNode()
        state = _build_state()
        node(state)

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    record = args[1]
    first = record["attempts"][0]
    assert first["verdict"] == "iterate"
    assert first["feedback"] != ""
    assert first["target_node"] == "problem_framer"


def test_max_retries_exhausted_forces_pass(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    max_retries = load_phase_config("phase1_understanding").critic.max_retries

    mock_llm.invoke.return_value = AIMessage(
        content=_verdict("iterate", "eda still missing missingness analysis", "data_analyst")
    )
    target_callable = MagicMock(return_value={})

    with patch("src.nodes.llm.analysis_critic.resolve_node", return_value=target_callable):
        node = AnalysisCriticNode()
        state = _build_state()
        node(state)

    assert mock_llm.invoke.call_count == max_retries + 1
    assert target_callable.call_count == max_retries

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    record = args[1]
    assert record["final_verdict"]["verdict"] == "pass"
    assert record["final_verdict"]["forced_pass"] is True


def test_validation_strategist_frozen_folds_forces_pass_without_crashing(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.return_value = AIMessage(
        content=_verdict("iterate", "recheck the CV strategy", "validation_strategist")
    )
    frozen_callable = MagicMock(
        side_effect=FoldsAlreadyFrozenError("fold_config.json already exists")
    )

    with patch("src.nodes.llm.analysis_critic.resolve_node", return_value=frozen_callable):
        node = AnalysisCriticNode()
        state = _build_state()

        # Must not raise, despite the target node raising FoldsAlreadyFrozenError.
        delta = node(state)

    assert mock_llm.invoke.call_count == 1
    frozen_callable.assert_called_once()
    assert set(delta.keys()) == {"messages"}

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    record = args[1]
    assert record["final_verdict"]["forced_pass"] is True
    assert record["final_verdict"]["folds_frozen"] is True


def test_verdict_always_normalized_when_llm_returns_garbage(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.side_effect = [
        AIMessage(content="not json at all {"),
        AIMessage(content=_verdict("pass", "ok", "data_analyst")),
    ]

    with patch(
        "src.nodes.llm.analysis_critic.resolve_node", return_value=MagicMock(return_value={})
    ):
        node = AnalysisCriticNode()
        state = _build_state()
        node(state)

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    record = args[1]
    first = record["attempts"][0]
    assert first["verdict"] == "iterate"
    assert first["feedback"] != ""
    assert "pars" in first["feedback"].lower()


def test_verdict_value_other_than_pass_or_iterate_normalizes_to_iterate(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.side_effect = [
        AIMessage(content=_verdict("reject", "needs rework", "problem_framer")),
        AIMessage(content=_verdict("pass", "ok", "problem_framer")),
    ]

    with patch(
        "src.nodes.llm.analysis_critic.resolve_node", return_value=MagicMock(return_value={})
    ):
        node = AnalysisCriticNode()
        state = _build_state()
        node(state)

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    record = args[1]
    assert record["attempts"][0]["verdict"] == "iterate"


def test_target_node_outside_allowed_set_falls_back_to_first_target(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.side_effect = [
        AIMessage(content=_verdict("iterate", "bad target name", "not_a_real_agent")),
        AIMessage(content=_verdict("pass", "ok", "data_analyst")),
    ]

    with patch(
        "src.nodes.llm.analysis_critic.resolve_node", return_value=MagicMock(return_value={})
    ):
        node = AnalysisCriticNode()
        state = _build_state()
        node(state)

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    record = args[1]
    assert record["attempts"][0]["target_node"] == PHASE1_TARGETS[0]


# -- content reading --


def test_missing_upstream_output_reads_as_not_yet_available(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok", "data_analyst"))

    with patch("src.nodes.llm.analysis_critic.resolve_node"):
        node = AnalysisCriticNode()
        state = _build_state()
        assert state["eda_report_path"] == ""

        node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    human_message = invoked_messages[-1]
    assert "not yet available" in human_message.content


def test_leakage_auditor_uses_fixed_relative_path(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok", "leakage_auditor"))

    with patch("src.nodes.llm.analysis_critic.resolve_node"):
        node = AnalysisCriticNode()
        state = _build_state()
        node(state)

    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_text.assert_any_call("reports/leakage_audit.json")


# -- __call__ return shape --


def test_call_state_delta_is_messages_only(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok", "data_analyst"))

    with patch("src.nodes.llm.analysis_critic.resolve_node"):
        node = AnalysisCriticNode()
        state = _build_state()
        delta = node(state)

    assert set(delta.keys()) == {"messages"}


def test_output_written_via_workspace_write_json(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok", "data_analyst"))

    with patch("src.nodes.llm.analysis_critic.resolve_node"):
        node = AnalysisCriticNode()
        state = _build_state(current_iteration=0)
        node(state)

    _, workspace_instance = mock_workspace_manager
    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "reports/critic_verdicts_phase1_understanding_iter0.json"
    payload = args[1]
    assert "attempts" in payload
    assert "final_verdict" in payload


# -- phase-aware output path (Phase 1 and Phase 4 must not collide) --


def test_phase1_and_phase4_write_to_different_files_at_same_iteration(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """Regression guard: both phases invoke `analysis_critic` while
    `current_iteration == 0` (nothing increments it between Phase 1 and
    Phase 4), so the write path must also vary by phase — an
    `{iteration}`-only pattern would make Phase 4's write silently clobber
    Phase 1's verdict record (code-quality review finding)."""
    _, workspace_instance = mock_workspace_manager

    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok", "data_analyst"))
    with patch("src.nodes.llm.analysis_critic.resolve_node"):
        node = AnalysisCriticNode()
        phase1_state = _build_state(current_iteration=0)
        node(phase1_state)
    phase1_path = workspace_instance.write_json.call_args[0][0]

    workspace_instance.write_json.reset_mock()
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok", "solution_architect"))
    with patch("src.nodes.llm.analysis_critic.resolve_node"):
        node = AnalysisCriticNode()
        phase4_state = _build_state(current_iteration=0)
        phase4_state["feature_spec_path"] = "reports/feature_spec.json"
        node(phase4_state)
    phase4_path = workspace_instance.write_json.call_args[0][0]

    assert phase1_path != phase4_path
    assert phase1_path == "reports/critic_verdicts_phase1_understanding_iter0.json"
    assert phase4_path == "reports/critic_verdicts_phase4_design_iter0.json"


# -- fence-stripping robustness --


def test_feedback_with_embedded_backtick_fence_parses(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """A single outer ```json fence whose `feedback` string value itself
    quotes a fenced code snippet must still parse correctly — the true
    closing fence is textually last, so the last-'```'-anchored candidate
    in `_fence_candidates` must find it (mirrors the sibling nodes'
    `test_json_string_value_containing_literal_backtick_fence_is_parsed`
    regression test)."""
    payload = {
        "verdict": "iterate",
        "feedback": "Wrap this in ```python\nfoo()\n``` fences to fix the leak",
        "target_node": "data_analyst",
    }
    content = f"```json\n{json.dumps(payload)}\n```"
    mock_llm.invoke.side_effect = [
        AIMessage(content=content),
        AIMessage(content=_verdict("pass", "ok", "data_analyst")),
    ]

    with patch(
        "src.nodes.llm.analysis_critic.resolve_node", return_value=MagicMock(return_value={})
    ):
        node = AnalysisCriticNode()
        state = _build_state()
        node(state)

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    first = args[1]["attempts"][0]
    assert first["verdict"] == "iterate"
    assert first["feedback"] == payload["feedback"]
    assert first["target_node"] == "data_analyst"


def test_response_with_stray_trailing_fenced_block_still_parses(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """Real bug found in adversarial review: if the LLM response contains
    more than one top-level fenced block (the intended ```json one, plus a
    stray narrative-fenced code example after it, despite the prompt's "no
    other prose" instruction), a single last-'```'-anchored strip anchors on
    the *wrong* (trailing) block's close and truncates/corrupts the real
    JSON — degrading gracefully to a parse-failure `iterate` rather than
    crashing, but silently discarding a legitimate verdict. The
    first-'```'-anchored fallback candidate in `_fence_candidates` must
    recover the real JSON instead."""
    content = (
        "```json\n"
        '{"verdict": "pass", "feedback": "looks good", "target_node": "data_analyst"}\n'
        "```\n\n"
        "Here's an example of what a fix might look like:\n\n"
        '```python\nprint("hi")\n```'
    )
    mock_llm.invoke.return_value = AIMessage(content=content)

    with patch("src.nodes.llm.analysis_critic.resolve_node"):
        node = AnalysisCriticNode()
        state = _build_state()
        node(state)

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    record = args[1]
    assert record["final_verdict"]["verdict"] == "pass"
    assert record["final_verdict"]["feedback"] == "looks good"
    assert "forced_pass" not in record["final_verdict"]


# -- FoldsAlreadyFrozenError scoping --


def test_folds_already_frozen_error_reraised_for_non_validation_strategist_target(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """The `FoldsAlreadyFrozenError` catch is scoped to `target_node ==
    "validation_strategist"` specifically — it must not silently swallow
    (and mislabel with `folds_frozen: True`) the same exception class if a
    future node reuses it for a different target (code-quality review
    finding)."""
    mock_llm.invoke.return_value = AIMessage(
        content=_verdict("iterate", "recheck the EDA", "data_analyst")
    )
    frozen_callable = MagicMock(side_effect=FoldsAlreadyFrozenError("unrelated write-once guard"))

    with patch("src.nodes.llm.analysis_critic.resolve_node", return_value=frozen_callable):
        node = AnalysisCriticNode()
        state = _build_state()

        with pytest.raises(FoldsAlreadyFrozenError):
            node(state)


# -- global safety cap --


def test_round_robin_targets_terminate_with_bounded_forced_pass(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """A pathological LLM that never says "pass", naming a *different*
    allowed target each cycle (round-robin), must still terminate with a
    forced pass within `max_total_cycles` `llm.invoke` calls.

    Note on which branch actually fires: with N = len(targets) allowed
    targets and this round-robin pattern, a pigeonhole argument (see the
    comment above `max_total_cycles` in analysis_critic.py) guarantees the
    per-target `count >= max_retries` guard trips no later than the final
    cycle — N targets cannot each stay under `max_retries + 1` visits
    across `N * (max_retries + 1)` cycles. So this test exercises the
    per-target forced-pass path, not the `for...else` global-cap branch;
    it asserts only the externally-observable guarantee (bounded, forced
    termination), not which internal branch produced it."""
    critic_config = load_phase_config("phase1_understanding").critic
    targets = critic_config.targets
    max_retries = critic_config.max_retries
    max_total_cycles = (max_retries + 1) * max(len(targets), 1)

    call_index = {"i": 0}

    def _invoke(_messages: Any) -> AIMessage:
        target = targets[call_index["i"] % len(targets)]
        call_index["i"] += 1
        return AIMessage(content=_verdict("iterate", f"fix {target}", target))

    mock_llm.invoke.side_effect = _invoke

    with patch(
        "src.nodes.llm.analysis_critic.resolve_node", return_value=MagicMock(return_value={})
    ):
        node = AnalysisCriticNode()
        state = _build_state()
        delta = node(state)

    assert mock_llm.invoke.call_count <= max_total_cycles
    assert set(delta.keys()) == {"messages"}

    _, workspace_instance = mock_workspace_manager
    args, _ = workspace_instance.write_json.call_args
    record = args[1]
    assert record["final_verdict"]["verdict"] == "pass"
    assert record["final_verdict"]["forced_pass"] is True
