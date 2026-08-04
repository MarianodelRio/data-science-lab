"""Unit tests for src/config/loaders.py."""

from pathlib import Path

import pytest

from src.config.errors import ConfigError
from src.config.loaders import load_agent_config, load_phase_config
from src.config.schema import CriticConfig

AGENTS_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "config" / "agents"
PHASES_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "config" / "phases"


def test_load_agent_config_parses_fixture() -> None:
    agent = load_agent_config("example_agent", base_dir=AGENTS_FIXTURES_DIR)

    assert agent.name == "solution_architect"
    assert agent.model_role == "reasoning"
    assert agent.prompt_version == "v1"
    assert agent.tools == ("rag", "workspace_reader")
    assert agent.output_file_pattern == "design/iteration_{iteration}/solution_plan.json"
    assert agent.max_tokens == 4096
    assert agent.temperature is None


def test_load_agent_config_missing_file_raises_filenotfounderror() -> None:
    with pytest.raises(FileNotFoundError):
        load_agent_config("does_not_exist", base_dir=AGENTS_FIXTURES_DIR)


def test_load_agent_config_missing_field_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="max_tokens"):
        load_agent_config("incomplete_agent", base_dir=AGENTS_FIXTURES_DIR)


def test_load_agent_config_tools_is_a_tuple() -> None:
    agent = load_agent_config("example_agent", base_dir=AGENTS_FIXTURES_DIR)

    assert isinstance(agent.tools, tuple)


def test_load_agent_config_falsy_max_tokens_is_not_rejected() -> None:
    """`max_tokens: 0` is falsy but valid — the required-field check must be
    `is None`, not truthiness.
    """
    agent = load_agent_config("zero_max_tokens_agent", base_dir=AGENTS_FIXTURES_DIR)

    assert agent.max_tokens == 0


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "../secret",
        "..",
        "a/b",
        "a\\b",
    ],
)
def test_load_agent_config_path_traversal_raises_config_error(name: str) -> None:
    with pytest.raises(ConfigError, match="[Ii]nvalid"):
        load_agent_config(name, base_dir=AGENTS_FIXTURES_DIR)


def test_load_phase_config_parses_fixture_with_critic() -> None:
    phase = load_phase_config("example_phase", base_dir=PHASES_FIXTURES_DIR)

    assert phase.name == "understanding"
    assert phase.nodes == (
        "data_analyst",
        "problem_framer",
        "validation_strategist",
        "leakage_auditor",
        "analysis_critic",
    )
    assert phase.sequence == phase.nodes
    assert phase.parallel_nodes == ()
    assert phase.interrupt_after is True
    assert isinstance(phase.critic, CriticConfig)
    assert phase.critic.node == "analysis_critic"
    assert phase.critic.targets == (
        "data_analyst",
        "problem_framer",
        "validation_strategist",
        "leakage_auditor",
    )
    assert phase.critic.max_retries == 3


def test_load_phase_config_without_critic_returns_none() -> None:
    phase = load_phase_config("phase_without_critic", base_dir=PHASES_FIXTURES_DIR)

    assert phase.name == "research"
    assert phase.critic is None
    assert phase.interrupt_after is False


def test_load_phase_config_missing_file_raises_filenotfounderror() -> None:
    with pytest.raises(FileNotFoundError):
        load_phase_config("does_not_exist", base_dir=PHASES_FIXTURES_DIR)


def test_load_phase_config_nodes_and_sequence_are_tuples() -> None:
    phase = load_phase_config("example_phase", base_dir=PHASES_FIXTURES_DIR)

    assert isinstance(phase.nodes, tuple)
    assert isinstance(phase.sequence, tuple)
    assert isinstance(phase.parallel_nodes, tuple)
    assert isinstance(phase.critic, CriticConfig)
    assert isinstance(phase.critic.targets, tuple)


def test_load_phase_config_critic_not_a_mapping_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="critic") as exc_info:
        load_phase_config("critic_not_mapping", base_dir=PHASES_FIXTURES_DIR)

    assert "mapping" in str(exc_info.value)
    assert not isinstance(exc_info.value, TypeError)


def test_load_phase_config_falsy_empty_lists_are_not_rejected() -> None:
    """Empty `nodes`/`sequence`/`parallel_nodes` lists and `interrupt_after:
    false` are falsy but valid — the required-field check must be `is None`,
    not truthiness.
    """
    phase = load_phase_config("empty_nodes_phase", base_dir=PHASES_FIXTURES_DIR)

    assert phase.nodes == ()
    assert phase.sequence == ()
    assert phase.parallel_nodes == ()
    assert phase.interrupt_after is False
    assert phase.critic is None


@pytest.mark.parametrize(
    "name",
    [
        "../../etc/passwd",
        "../secret",
        "..",
        "a/b",
        "a\\b",
    ],
)
def test_load_phase_config_path_traversal_raises_config_error(name: str) -> None:
    with pytest.raises(ConfigError, match="[Ii]nvalid"):
        load_phase_config(name, base_dir=PHASES_FIXTURES_DIR)
