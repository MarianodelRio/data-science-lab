"""Unit tests for src/nodes/llm/code_critic.py.

`code_critic` overrides `LLMNode.__call__` wholesale and instantiates
`WorkspaceManager` directly inside its own module (not via the base class), so
`WorkspaceManager` is patched at `src.nodes.llm.code_critic`.

`resolve_node`, unlike in `test_analysis_critic.py`, is patched at
**`src.graph.node_resolver`**: the node reads it as a module attribute at call
time (B-001's binding style, mirroring `src/graph/builder.py`), so
`src.nodes.llm.code_critic.resolve_node` deliberately does not exist and
patching it would raise `AttributeError`.

`LLMFactory`/`Settings` are still patched at `src.nodes.llm.base` since
`LLMNode.__init__` is inherited unchanged. No network calls, no real filesystem
writes, no real node re-invocation (`resolve_node` is always mocked).
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
from src.config.schema import PhaseConfig
from src.config.settings import ContextConfig, Settings
from src.nodes.llm.code_critic import CodeCriticNode
from src.state import new_state

PHASE_STEM = "phase5_implementation"
PHASE5_TARGETS = ("coder",)

_SAMPLE_TRAIN_PY = (
    "import pandas as pd\n"
    "from pathlib import Path\n\n"
    "def main() -> None:\n"
    '    df = pd.read_csv(Path("data/raw/train.csv"))\n'
    "    print(len(df))\n\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
)

_ABSOLUTE_PATH_LITERAL = "/home/mariano/data/train.csv"
_LEAKY_TRAIN_PY = (
    "import pandas as pd\n\n"
    "def main() -> None:\n"
    f'    df = pd.read_csv("{_ABSOLUTE_PATH_LITERAL}")\n'
    "    print(len(df))\n"
)


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


def _verdict(verdict: str, feedback: str, target_node: str | None = None) -> str:
    payload: dict[str, Any] = {"verdict": verdict, "feedback": feedback}
    if target_node is not None:
        payload["target_node"] = target_node
    return json.dumps(payload)


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok"))
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
    """Patched at `src.nodes.llm.code_critic` — the node's custom `__call__`
    constructs `WorkspaceManager` directly in that module, never going through
    the base class's own `__call__`."""
    instance = MagicMock()
    instance.workspace_path = Path("/workspace")
    instance.read_text.return_value = _SAMPLE_TRAIN_PY
    instance.write_json.return_value = "/workspace/reports/code_critic_verdicts_iter0.json"
    with patch("src.nodes.llm.code_critic.WorkspaceManager") as mock_wm_cls:
        mock_wm_cls.return_value = instance
        yield mock_wm_cls, instance


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    return state


def _record(workspace_instance: MagicMock) -> dict[str, Any]:
    args, _ = workspace_instance.write_json.call_args
    return args[1]


def _sent_human_message(mock_llm: MagicMock, call_index: int = 0) -> str:
    """Content of the trailing `HumanMessage` of the given `llm.invoke` call."""
    messages = mock_llm.invoke.call_args_list[call_index][0][0]
    return str(messages[-1].content)


def _train_py_reads(workspace_instance: MagicMock) -> list[str]:
    return [
        call.args[0]
        for call in workspace_instance.read_text.call_args_list
        if call.args and str(call.args[0]).endswith("/train.py")
    ]


# -- config / prompt load (Done-when #4) --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("code_critic")

    assert config.name == "code_critic"
    assert config.model_role == "implementation"
    assert config.prompt_version == "v1"
    assert config.tools == ()
    assert config.output_file_pattern == "reports/code_critic_verdicts_iter{iteration}.json"
    assert config.max_tokens == 2048

    prompt = PromptLoader().load("code_critic", "v1")
    assert prompt.strip() != ""
    # The `# System prompt — {name}` header is the dispatch key
    # `tests/fixtures/graph_mocks.py::_make_llm_side_effect` matches on.
    assert prompt.splitlines()[0] == "# System prompt — code_critic"
    for token in ("verdict", "feedback", "relative", "seed"):
        assert token in prompt


# -- verdict / feedback / retry mechanics (Done-when critical) --


def test_iterate_verdict_yields_nonempty_feedback(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """Done-when #1: a mock LLM returning `iterate` yields non-empty feedback."""
    feedback = "Seed LGBMClassifier with the fold_config.json seed instead of leaving it unset."
    mock_llm.invoke.side_effect = [
        AIMessage(content=_verdict("iterate", feedback)),
        AIMessage(content=_verdict("pass", "looks good now")),
    ]

    with patch("src.graph.node_resolver.resolve_node", return_value=MagicMock(return_value={})):
        node = CodeCriticNode()
        node(_build_state())

    _, workspace_instance = mock_workspace_manager
    first = _record(workspace_instance)["attempts"][0]
    assert first["verdict"] == "iterate"
    assert first["feedback"] == feedback
    assert first["feedback"].strip() != ""
    assert first["target_node"] == "coder"


def test_hardcoded_absolute_path_sample_is_flagged(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """Done-when #3: a code sample carrying a hardcoded absolute path reaches
    the prompt verbatim and the (mocked) verdict flags it as `iterate`."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_text.return_value = _LEAKY_TRAIN_PY

    feedback = (
        f"train.py loads the dataset from the absolute path {_ABSOLUTE_PATH_LITERAL}. "
        "Replace it with a workspace-relative Path('data/raw/train.csv')."
    )
    mock_llm.invoke.side_effect = [
        AIMessage(content=_verdict("iterate", feedback)),
        AIMessage(content=_verdict("pass", "absolute path removed")),
    ]

    with patch("src.graph.node_resolver.resolve_node", return_value=MagicMock(return_value={})):
        node = CodeCriticNode()
        node(_build_state())

    # The offending literal really reaches the reviewing LLM.
    assert _ABSOLUTE_PATH_LITERAL in _sent_human_message(mock_llm)

    first = _record(workspace_instance)["attempts"][0]
    assert first["verdict"] == "iterate"
    assert _ABSOLUTE_PATH_LITERAL in first["feedback"]


def test_max_retries_exhausted_forces_pass(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """Done-when #2 / CLAUDE.md invariant #5: the budget comes from the phase
    YAML's `critic.max_retries`, never a literal in this test."""
    critic_config = load_phase_config(PHASE_STEM).critic
    assert critic_config is not None
    max_retries = critic_config.max_retries

    mock_llm.invoke.return_value = AIMessage(
        content=_verdict("iterate", "still reading the CSV from an absolute path")
    )
    target_callable = MagicMock(return_value={})

    with patch(
        "src.graph.node_resolver.resolve_node", return_value=target_callable
    ) as mock_resolve:
        node = CodeCriticNode()
        node(_build_state())

    assert mock_llm.invoke.call_count == max_retries + 1
    assert target_callable.call_count == max_retries
    mock_resolve.assert_called_with("coder")

    _, workspace_instance = mock_workspace_manager
    final = _record(workspace_instance)["final_verdict"]
    assert final["verdict"] == "pass"
    assert final["forced_pass"] is True


def test_garbage_llm_response_normalizes_to_iterate(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.side_effect = [
        AIMessage(content="not json at all {"),
        AIMessage(content=_verdict("pass", "ok")),
    ]

    with patch("src.graph.node_resolver.resolve_node", return_value=MagicMock(return_value={})):
        node = CodeCriticNode()
        node(_build_state())

    _, workspace_instance = mock_workspace_manager
    first = _record(workspace_instance)["attempts"][0]
    assert first["verdict"] == "iterate"
    assert first["feedback"].strip() != ""
    assert "pars" in first["feedback"].lower()


def test_verdict_value_other_than_pass_or_iterate_normalizes_to_iterate(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.side_effect = [
        AIMessage(content=_verdict("reject", "needs rework")),
        AIMessage(content=_verdict("pass", "ok")),
    ]

    with patch("src.graph.node_resolver.resolve_node", return_value=MagicMock(return_value={})):
        node = CodeCriticNode()
        node(_build_state())

    _, workspace_instance = mock_workspace_manager
    assert _record(workspace_instance)["attempts"][0]["verdict"] == "iterate"


def test_blank_feedback_is_replaced_with_non_empty_default(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.side_effect = [
        AIMessage(content=_verdict("iterate", "   ")),
        AIMessage(content=_verdict("pass", "ok")),
    ]

    with patch("src.graph.node_resolver.resolve_node", return_value=MagicMock(return_value={})):
        node = CodeCriticNode()
        node(_build_state())

    _, workspace_instance = mock_workspace_manager
    first = _record(workspace_instance)["attempts"][0]
    assert first["verdict"] == "iterate"
    assert first["feedback"].strip() != ""


def test_unrecognized_target_node_falls_back_to_the_single_allowed_target(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.side_effect = [
        AIMessage(content=_verdict("iterate", "fix it", target_node="not_a_real_agent")),
        AIMessage(content=_verdict("pass", "ok")),
    ]

    with patch("src.graph.node_resolver.resolve_node", return_value=MagicMock(return_value={})):
        node = CodeCriticNode()
        node(_build_state())

    _, workspace_instance = mock_workspace_manager
    assert _record(workspace_instance)["attempts"][0]["target_node"] == PHASE5_TARGETS[0]


# -- JSON extraction robustness (reuses `_experiment_design.extract_json_object`) --


def test_feedback_with_embedded_backtick_fence_parses(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """The true closing fence is textually last, so a ```python block quoted
    *inside* the `feedback` string value must not be mistaken for it —
    `_strip_outer_fence`'s `rfind("```")` anchor handles this."""
    payload = {
        "verdict": "iterate",
        "feedback": "Replace it with ```python\nPath('data/raw/train.csv')\n``` instead.",
    }
    mock_llm.invoke.side_effect = [
        AIMessage(content=f"```json\n{json.dumps(payload)}\n```"),
        AIMessage(content=_verdict("pass", "ok")),
    ]

    with patch("src.graph.node_resolver.resolve_node", return_value=MagicMock(return_value={})):
        node = CodeCriticNode()
        node(_build_state())

    _, workspace_instance = mock_workspace_manager
    first = _record(workspace_instance)["attempts"][0]
    assert first["verdict"] == "iterate"
    assert first["feedback"] == payload["feedback"]


def test_response_with_prose_and_stray_trailing_fence_still_parses(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """Prose preamble plus a stray trailing fenced block: the first-`{`-to-
    last-`}` salvage inside `extract_json_object` recovers the real verdict."""
    content = (
        "Here is my review of the script.\n\n"
        '{"verdict": "pass", "feedback": "looks good"}\n\n'
        "For reference, the idiom I mean is:\n\n"
        '```python\nprint("hi")\n```'
    )
    mock_llm.invoke.return_value = AIMessage(content=content)

    with patch("src.graph.node_resolver.resolve_node"):
        node = CodeCriticNode()
        node(_build_state())

    _, workspace_instance = mock_workspace_manager
    final = _record(workspace_instance)["final_verdict"]
    assert final["verdict"] == "pass"
    assert final["feedback"] == "looks good"
    assert "forced_pass" not in final


# -- artifact reads degrade, never crash --


def test_unreadable_train_py_degrades_without_raising(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_text.side_effect = FileNotFoundError("no train.py")
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "nothing to review"))

    with patch("src.graph.node_resolver.resolve_node"):
        node = CodeCriticNode()
        delta = node(_build_state())

    assert "unable to read" in _sent_human_message(mock_llm)
    assert set(delta.keys()) == {"messages"}
    assert _record(workspace_instance)["attempts"][0]["code_available"] is False


@pytest.mark.parametrize(
    "error",
    [
        ValueError("relative_path must be relative, got absolute path"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        RecursionError("maximum recursion depth exceeded"),
        OSError("is a directory"),
    ],
)
def test_corrupt_or_out_of_workspace_artifact_degrades(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm, error: Exception
) -> None:
    """Proves the readers catch `DEGRADE_ERRORS`, not a bare `OSError`: three
    of these four are not `OSError` subclasses at all."""
    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_text.side_effect = error
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "nothing to review"))

    with patch("src.graph.node_resolver.resolve_node"):
        node = CodeCriticNode()
        node(_build_state())

    sent = _sent_human_message(mock_llm)
    assert "unable to read the generated training code" in sent
    assert "design.json not available" in sent
    assert "results.json not available" in sent


def test_long_generated_code_is_truncated_before_prompting(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    _, workspace_instance = mock_workspace_manager

    def _read(relative_path: str) -> str:
        return "x" * 60_000 if relative_path.endswith("/train.py") else "{}"

    workspace_instance.read_text.side_effect = _read
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok"))

    with patch("src.graph.node_resolver.resolve_node"):
        node = CodeCriticNode()
        node(_build_state())

    sent = _sent_human_message(mock_llm)
    assert "truncated at" in sent
    assert "x" * 60_000 not in sent
    assert len(sent) < 25_000


def test_all_artifact_sections_present_in_prompt(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    _, workspace_instance = mock_workspace_manager

    def _read(relative_path: str) -> str:
        return f"CONTENT OF {relative_path}"

    workspace_instance.read_text.side_effect = _read
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok"))

    with patch("src.graph.node_resolver.resolve_node"):
        node = CodeCriticNode()
        node(_build_state())

    sent = _sent_human_message(mock_llm)
    assert "## Generated training code (train.py)" in sent
    assert "## Experiment design (design.json)" in sent
    assert "## Experiment results (results.json)" in sent
    assert "## Frozen CV folds" in sent
    # All three files come from the same experiment directory.
    for filename in ("train.py", "design.json", "results.json"):
        workspace_instance.read_text.assert_any_call(f"experiments/exp_0/{filename}")


# -- experiment-directory resolution --


@pytest.mark.parametrize(
    "recorded_path",
    [
        "experiments/exp_7",
        "/workspace/experiments/exp_7",
        "/workspace/experiments/exp_7/results.json",
    ],
)
def test_state_experiment_path_is_preferred_over_well_known_dir(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm, recorded_path: str
) -> None:
    """Covers the directory form, the absolute form (re-relativized) and the
    file form (parent taken) — `coder` (T-029) has not fixed which it records."""
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok"))
    state = _build_state()
    state["experiments"] = [{"path": recorded_path}]

    with patch("src.graph.node_resolver.resolve_node"):
        node = CodeCriticNode()
        node(state)

    _, workspace_instance = mock_workspace_manager
    workspace_instance.read_text.assert_any_call("experiments/exp_7/train.py")


@pytest.mark.parametrize(
    "experiments",
    [
        [],
        [{}],
        [{"path": ""}],
        [{"path": None}],
        [{"path": 123}],
        [{"path": "../escape"}],
        [{"path": "/elsewhere/exp_1"}],
        ["not-a-dict"],
        "not-a-list",
    ],
)
def test_unusable_state_experiment_path_falls_back_to_well_known_dir(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm, experiments: Any
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok"))
    state = _build_state()
    state["experiments"] = experiments

    with patch("src.graph.node_resolver.resolve_node"):
        node = CodeCriticNode()
        node(state)

    _, workspace_instance = mock_workspace_manager
    assert _train_py_reads(workspace_instance) == ["experiments/exp_0/train.py"]


def test_regenerated_code_is_reread_after_the_target_runs(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """`working_state.update(target_delta)` is what lets the next review cycle
    pick up the regenerated script when `coder` moves the recorded path."""
    mock_llm.invoke.side_effect = [
        AIMessage(content=_verdict("iterate", "rewrite it")),
        AIMessage(content=_verdict("pass", "better")),
    ]
    target_callable = MagicMock(return_value={"experiments": [{"path": "experiments/exp_9"}]})

    with patch("src.graph.node_resolver.resolve_node", return_value=target_callable):
        node = CodeCriticNode()
        node(_build_state())

    _, workspace_instance = mock_workspace_manager
    assert _train_py_reads(workspace_instance) == [
        "experiments/exp_0/train.py",
        "experiments/exp_9/train.py",
    ]


# -- __call__ return shape and output record --


def test_call_state_delta_is_messages_only(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    """The target's non-`messages` delta feeds `working_state` only — it must
    never leak into this node's own returned delta."""
    coder_message = AIMessage(content="coder ran")
    mock_llm.invoke.side_effect = [
        AIMessage(content=_verdict("iterate", "rewrite it")),
        AIMessage(content=_verdict("pass", "better")),
    ]
    target_callable = MagicMock(
        return_value={"experiments": [{"path": "experiments/exp_1"}], "messages": [coder_message]}
    )

    with patch("src.graph.node_resolver.resolve_node", return_value=target_callable):
        node = CodeCriticNode()
        delta = node(_build_state())

    assert set(delta.keys()) == {"messages"}
    assert coder_message in delta["messages"]
    assert len(delta["messages"]) == 3


def test_output_written_via_write_json_at_iteration_path(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    mock_llm.invoke.return_value = AIMessage(content=_verdict("pass", "ok"))

    with patch("src.graph.node_resolver.resolve_node"):
        node = CodeCriticNode()
        node(_build_state(current_iteration=0))

    _, workspace_instance = mock_workspace_manager
    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "reports/code_critic_verdicts_iter0.json"
    record = args[1]
    assert record["phase"] == PHASE_STEM
    assert record["targets"] == list(PHASE5_TARGETS)
    assert record["attempts"]
    assert record["final_verdict"] == record["attempts"][-1]


def test_missing_critic_block_raises(
    patched_llm_factory, patched_settings, mock_workspace_manager, mock_llm
) -> None:
    criticless = PhaseConfig(
        name="implementation",
        nodes=("specialist_selector", "coder", "code_critic"),
        sequence=("specialist_selector", "coder", "code_critic"),
        parallel_nodes=(),
        critic=None,
        interrupt_after=False,
    )

    with patch("src.nodes.llm.code_critic.load_phase_config", return_value=criticless):
        node = CodeCriticNode()
        with pytest.raises(ValueError, match="critic"):
            node(_build_state())
