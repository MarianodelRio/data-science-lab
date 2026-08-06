"""Unit tests for src/nodes/llm/data_analyst.py.

All external calls are mocked: `LLMFactory`/the LLM itself, `WorkspaceManager`
(patched at their import location inside `src.nodes.llm.base`, matching
`test_base.py`'s convention), and `execute` (patched at its import location
inside `src.nodes.llm.data_analyst`, since it is imported there via
`from src.tools.code_executor import execute`). No network calls, no real
filesystem writes, and no subprocess is ever spawned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.config.loaders import load_agent_config
from src.config.prompts import PromptLoader
from src.config.settings import ContextConfig, Settings
from src.nodes.llm.data_analyst import DataAnalystNode
from src.state import new_state
from src.tools.code_executor import ExecResult

NARRATIVE = (
    "## Dataset overview\n\nOne file found: `data/raw/train.csv` with 100 rows and 5 columns."
)
CODE = 'import pandas as pd\nprint(pd.read_csv("data/raw/train.csv").shape)'
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
        instance.write_text.return_value = "/workspace/reports/eda_report.md"
        instance.write_notebook.return_value = "/workspace/notebooks/01_eda.ipynb"
        mock_wm_cls.return_value = instance
        yield mock_wm_cls, instance


@pytest.fixture
def patched_execute():
    with patch("src.nodes.llm.data_analyst.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=0, stdout="(100, 5)", stderr="", timed_out=False
        )
        yield mock_execute


def _build_state(current_iteration: int = 0) -> dict[str, Any]:
    state = new_state("comp", "/workspace")
    state["current_iteration"] = current_iteration
    return state


# -- config / prompt load --


def test_config_and_prompt_load_for_real() -> None:
    config = load_agent_config("data_analyst")

    assert config.name == "data_analyst"
    assert config.model_role == "reasoning"
    assert config.prompt_version == "v1"
    assert config.tools == ("code_executor",)
    assert config.output_file_pattern == "reports/eda_report.md"
    assert config.max_tokens == 4096

    prompt = PromptLoader().load("data_analyst", "v1")
    assert prompt.strip() != ""
    assert "```python" in prompt


# -- __call__ behavior --


def test_call_writes_eda_report_via_workspace_write_text(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = DataAnalystNode()
    state = _build_state()

    node(state)

    workspace_instance.write_text.assert_called_once()
    args, _ = workspace_instance.write_text.call_args
    assert args[0] == "reports/eda_report.md"
    assert NARRATIVE in args[1]
    assert "## Execution output" in args[1]


def test_call_writes_notebook_with_code_and_markdown_cells(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = DataAnalystNode()
    state = _build_state()

    node(state)

    workspace_instance.write_notebook.assert_called_once_with(
        "notebooks/01_eda.ipynb",
        cells=[
            {"cell_type": "markdown", "source": NARRATIVE},
            {"cell_type": "code", "source": CODE},
        ],
    )


def test_call_executes_code_via_code_executor_not_inline(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    node = DataAnalystNode()
    state = _build_state()

    node(state)

    patched_execute.assert_called_once_with(CODE, cwd="/workspace")

    source_path = Path(__file__).resolve().parents[4] / "src" / "nodes" / "llm" / "data_analyst.py"
    source = source_path.read_text(encoding="utf-8")
    assert "exec(" not in source
    assert "eval(" not in source


def test_call_sets_eda_report_path_in_state_delta(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    _, workspace_instance = mock_workspace_manager
    node = DataAnalystNode()
    state = _build_state()

    delta = node(state)

    assert delta["eda_report_path"] == workspace_instance.write_text.return_value
    assert set(delta.keys()) == {"messages", "eda_report_path"}


def test_report_includes_execution_errors_section_on_nonzero_returncode(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    with patch("src.nodes.llm.data_analyst.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=1, stdout="", stderr="Traceback: boom", timed_out=False
        )
        _, workspace_instance = mock_workspace_manager
        node = DataAnalystNode()
        state = _build_state()

        node(state)

    written_content = workspace_instance.write_text.call_args[0][1]
    assert "## Execution errors" in written_content
    assert "Traceback: boom" in written_content


def test_report_includes_timeout_note_when_timed_out(
    patched_llm_factory, patched_settings, mock_workspace_manager
) -> None:
    with patch("src.nodes.llm.data_analyst.execute") as mock_execute:
        mock_execute.return_value = ExecResult(
            returncode=-1, stdout="partial", stderr="", timed_out=True
        )
        _, workspace_instance = mock_workspace_manager
        node = DataAnalystNode()
        state = _build_state()

        node(state)

    written_content = workspace_instance.write_text.call_args[0][1]
    assert "timed_out: True" in written_content


def test_missing_code_fence_raises_value_error(
    patched_llm_factory, patched_settings, mock_workspace_manager, patched_execute
) -> None:
    mock_llm = patched_llm_factory.get.return_value
    mock_llm.invoke.return_value = AIMessage(content="Just narrative, no code fence at all.")
    node = DataAnalystNode()
    state = _build_state()

    with pytest.raises(ValueError, match="no fenced"):
        node(state)
