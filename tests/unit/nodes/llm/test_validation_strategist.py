"""Unit tests for src/nodes/llm/validation_strategist.py.

All external calls are mocked: `LLMFactory`/the LLM itself, `WorkspaceManager`
(patched at their import location inside `src.nodes.llm.base`, matching
`test_base.py`'s convention), and `execute` (patched at its import location
inside `src.nodes.llm.validation_strategist`, since it is imported there via
`from src.tools.code_executor import execute`). No network calls, and no real
subprocess is ever spawned. One test uses a real `WorkspaceManager` against a
`tmp_path` to prove the write-once guard leaves the file byte-identical, but
`execute` is still mocked there too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sklearn.model_selection import KFold

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings
from src.nodes.llm.errors import FoldsAlreadyFrozenError
from src.nodes.llm.validation_strategist import ValidationStrategistNode
from src.state import new_state
from src.tools.code_executor import ExecResult
from src.workspace.workspace_manager import WorkspaceManager

FOLD_PAYLOAD = {
    "strategy": "stratified",
    "n_folds": 3,
    "seed": 42,
    "fold_indices": [
        {"train": [0, 1, 2, 3], "val": [4, 5]},
        {"train": [0, 1, 4, 5], "val": [2, 3]},
        {"train": [2, 3, 4, 5], "val": [0, 1]},
    ],
}
NARRATIVE = "## Strategy chosen\n\nStratified K-fold on the target column."
CODE = f"import json\nprint(json.dumps({FOLD_PAYLOAD!r}))"
RESPONSE_CONTENT = f"{NARRATIVE}\n\n```python\n{CODE}\n```\n"


def _make_settings(max_messages_per_node: int = 10) -> MagicMock:
    settings = MagicMock(spec=Settings)
    settings.context = ContextConfig(
        trim_strategy="last_n_messages", max_messages_per_node=max_messages_per_node
    )
    return settings


@pytest.fixture
def mock_llm() -> MagicMock:
    llm = MagicMock()
    llm.invoke.return_value = AIMessage(content=RESPONSE_CONTENT)
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
    with patch("src.nodes.llm.base.WorkspaceManager") as mock_wm_cls:
        instance = MagicMock()
        instance.workspace_path = Path("/workspace")
        instance.write_json.return_value = "/workspace/validation/fold_config.json"
        mock_wm_cls.return_value = instance
        yield mock_wm_cls, instance


@pytest.fixture
def patched_execute():
    with patch("src.nodes.llm.validation_strategist.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0, stdout=json.dumps(FOLD_PAYLOAD), stderr="", timed_out=False
        )
        yield mock_execute


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    return state


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("validation_strategist")

    assert config.name == "validation_strategist"
    assert config.model_role == "fast"
    assert config.prompt_version == "v1"
    assert config.tools == ("code_executor",)
    assert config.output_file_pattern == "validation/fold_config.json"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("validation_strategist", "v1")
    assert prompt.strip() != ""
    assert "```python" in prompt


# -- __call__ behavior --


def test_call_writes_fold_config_with_strategy_and_fold_indices(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ValidationStrategistNode()
    state = _build_state()

    node(state)

    workspace_instance.write_json.assert_called_once()
    args, _ = workspace_instance.write_json.call_args
    assert args[0] == "validation/fold_config.json"
    written = args[1]
    assert written["strategy"] == "stratified"
    assert written["fold_indices"] == FOLD_PAYLOAD["fold_indices"]
    assert written["n_folds"] == 3
    assert written["seed"] == 42


def test_call_sets_validation_config_path_in_state_delta(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = ValidationStrategistNode()
    state = _build_state()

    delta = node(state)

    assert delta["validation_config_path"] == workspace_instance.write_json.return_value
    assert set(delta.keys()) == {"messages", "validation_config_path"}


def test_write_json_drops_extra_keys_from_llm_stdout(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    payload = {**FOLD_PAYLOAD, "target_column": "y"}
    with patch("src.nodes.llm.validation_strategist.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0, stdout=json.dumps(payload), stderr="", timed_out=False
        )
        _, workspace_instance = mock_workspace_manager
        node = ValidationStrategistNode()
        state = _build_state()

        node(state)

    args, _ = workspace_instance.write_json.call_args
    written = args[1]
    assert set(written.keys()) == {"strategy", "n_folds", "fold_indices", "seed"}


def test_call_executes_code_via_code_executor_not_inline(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    node = ValidationStrategistNode()
    state = _build_state()

    node(state)

    patched_execute.assert_called_once_with(CODE, cwd="/workspace")

    source_path = (
        Path(__file__).resolve().parents[4] / "src" / "nodes" / "llm" / "validation_strategist.py"
    )
    source = source_path.read_text(encoding="utf-8")
    assert "exec(" not in source
    assert "eval(" not in source


def test_missing_code_fence_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="Just narrative, no code fence at all.")
    node = ValidationStrategistNode()
    state = _build_state()

    with pytest.raises(ValueError, match="no fenced"):
        node(state)


def test_multiple_code_fences_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(
        content=(
            f"{NARRATIVE}\n\n```python\n{CODE}\n```\n\n"
            "Here's an illustrative example of an alternative approach:\n\n"
            "```python\nprint('this second block should not be silently kept')\n```\n"
        )
    )
    node = ValidationStrategistNode()
    state = _build_state()

    with pytest.raises(ValueError, match="expected exactly one"):
        node(state)


def test_execution_failure_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    with patch("src.nodes.llm.validation_strategist.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=1, stdout="", stderr="Traceback: boom", timed_out=False
        )
        _, workspace_instance = mock_workspace_manager
        node = ValidationStrategistNode()
        state = _build_state()

        with pytest.raises(ValueError, match="execution failed"):
            node(state)

    workspace_instance.write_json.assert_not_called()


def test_execution_timeout_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    with patch("src.nodes.llm.validation_strategist.execute") as mock_execute:
        mock_execute.return_value = ExecResult(returncode=-1, stdout="", stderr="", timed_out=True)
        _, workspace_instance = mock_workspace_manager
        node = ValidationStrategistNode()
        state = _build_state()

        with pytest.raises(ValueError, match="execution failed"):
            node(state)

    workspace_instance.write_json.assert_not_called()


def test_invalid_json_stdout_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    with patch("src.nodes.llm.validation_strategist.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0, stdout="not json at all", stderr="", timed_out=False
        )
        _, workspace_instance = mock_workspace_manager
        node = ValidationStrategistNode()
        state = _build_state()

        with pytest.raises(ValueError, match="did not print a single JSON object"):
            node(state)

    workspace_instance.write_json.assert_not_called()


def test_missing_required_key_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    incomplete = {k: v for k, v in FOLD_PAYLOAD.items() if k != "seed"}
    with patch("src.nodes.llm.validation_strategist.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0, stdout=json.dumps(incomplete), stderr="", timed_out=False
        )
        _, workspace_instance = mock_workspace_manager
        node = ValidationStrategistNode()
        state = _build_state()

        with pytest.raises(ValueError, match="missing required keys"):
            node(state)

    workspace_instance.write_json.assert_not_called()


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"fold_indices": []}, id="empty-fold-indices"),
        pytest.param({"n_folds": 5}, id="n_folds-mismatch-with-fold-indices-length"),
        pytest.param(
            {"fold_indices": [{"train": [0, 1]}]},
            id="fold-entry-missing-val-key",
        ),
        pytest.param(
            {"fold_indices": [{"train": [0, 1], "val": "not-a-list"}]},
            id="fold-entry-val-not-a-list",
        ),
        pytest.param({"n_folds": "3"}, id="n_folds-not-an-int"),
        pytest.param({"seed": "42"}, id="seed-not-an-int"),
    ],
)
def test_malformed_fold_indices_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, overrides: dict
) -> None:
    payload = {**FOLD_PAYLOAD, **overrides}
    with patch("src.nodes.llm.validation_strategist.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0, stdout=json.dumps(payload), stderr="", timed_out=False
        )
        _, workspace_instance = mock_workspace_manager
        node = ValidationStrategistNode()
        state = _build_state()

        with pytest.raises(ValueError):
            node(state)

    workspace_instance.write_json.assert_not_called()


# -- upstream context injection --


def test_build_messages_includes_upstream_context(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute, tmp_path
) -> None:
    problem_path = tmp_path / "problem.md"
    problem_path.write_text("PROBLEM_DEFINITION_MARKER: binary classification", encoding="utf-8")
    eda_path = tmp_path / "eda.md"
    eda_path.write_text("EDA_REPORT_MARKER: target is balanced", encoding="utf-8")

    mock_llm = patched_llm_factory.get.return_value
    node = ValidationStrategistNode()
    state = _build_state()
    state["problem_definition_path"] = str(problem_path)
    state["eda_report_path"] = str(eda_path)

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert isinstance(last_message, HumanMessage)
    assert "PROBLEM_DEFINITION_MARKER: binary classification" in last_message.content
    assert "EDA_REPORT_MARKER: target is balanced" in last_message.content


def test_build_messages_handles_missing_upstream_paths(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    node = ValidationStrategistNode()
    state = _build_state()
    assert state["problem_definition_path"] == ""
    assert state["eda_report_path"] == ""

    node(state)

    invoked_messages = mock_llm.invoke.call_args[0][0]
    last_message = invoked_messages[-1]
    assert isinstance(last_message, HumanMessage)
    assert "not yet available" in last_message.content


# -- write-once guard --


def test_calling_twice_raises_folds_already_frozen_and_file_is_byte_identical(
    patched_llm_factory, patched_settings, tmp_path
) -> None:
    with patch("src.nodes.llm.validation_strategist.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0, stdout=json.dumps(FOLD_PAYLOAD), stderr="", timed_out=False
        )
        with patch("src.nodes.llm.base.WorkspaceManager", side_effect=WorkspaceManager):
            node = ValidationStrategistNode()
            state = _build_state()
            state["workspace_path"] = str(tmp_path)

            delta = node(state)

            written_file = Path(delta["validation_config_path"])
            assert written_file.exists()
            original_bytes = written_file.read_bytes()

            with pytest.raises(FoldsAlreadyFrozenError):
                node(state)

            assert written_file.read_bytes() == original_bytes

        mock_execute.assert_called_once()


def test_fold_indices_partition_coverage(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    n_rows = 9
    kfold = KFold(n_splits=3, shuffle=True, random_state=42)
    fold_indices = [
        {"train": train.tolist(), "val": val.tolist()} for train, val in kfold.split(range(n_rows))
    ]
    payload = {
        "strategy": "stratified",
        "n_folds": 3,
        "seed": 42,
        "fold_indices": fold_indices,
    }

    with patch("src.nodes.llm.validation_strategist.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0, stdout=json.dumps(payload), stderr="", timed_out=False
        )
        _, workspace_instance = mock_workspace_manager
        node = ValidationStrategistNode()
        state = _build_state()

        node(state)

    args, _ = workspace_instance.write_json.call_args
    written = args[1]

    all_val_indices: list[int] = []
    for fold in written["fold_indices"]:
        all_val_indices.extend(fold["val"])

    assert sorted(all_val_indices) == list(range(n_rows))
    assert len(all_val_indices) == len(set(all_val_indices))
