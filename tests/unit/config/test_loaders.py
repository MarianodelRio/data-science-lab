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
    assert agent.tools == ["rag", "workspace_reader"]
    assert agent.output_file_pattern == "design/iteration_{iteration}/solution_plan.json"
    assert agent.max_tokens == 4096
    assert agent.temperature is None


def test_load_agent_config_missing_file_raises_filenotfounderror() -> None:
    with pytest.raises(FileNotFoundError):
        load_agent_config("does_not_exist", base_dir=AGENTS_FIXTURES_DIR)


def test_load_agent_config_missing_field_raises_config_error() -> None:
    with pytest.raises(ConfigError, match="max_tokens"):
        load_agent_config("incomplete_agent", base_dir=AGENTS_FIXTURES_DIR)


def test_load_phase_config_parses_fixture_with_critic() -> None:
    phase = load_phase_config("example_phase", base_dir=PHASES_FIXTURES_DIR)

    assert phase.name == "understanding"
    assert phase.nodes == [
        "data_analyst",
        "problem_framer",
        "validation_strategist",
        "leakage_auditor",
        "analysis_critic",
    ]
    assert phase.sequence == phase.nodes
    assert phase.parallel_nodes == []
    assert phase.interrupt_after is True
    assert isinstance(phase.critic, CriticConfig)
    assert phase.critic.node == "analysis_critic"
    assert phase.critic.targets == [
        "data_analyst",
        "problem_framer",
        "validation_strategist",
        "leakage_auditor",
    ]
    assert phase.critic.max_retries == 3


def test_load_phase_config_without_critic_returns_none() -> None:
    phase = load_phase_config("phase_without_critic", base_dir=PHASES_FIXTURES_DIR)

    assert phase.name == "research"
    assert phase.critic is None
    assert phase.interrupt_after is False


def test_load_phase_config_missing_file_raises_filenotfounderror() -> None:
    with pytest.raises(FileNotFoundError):
        load_phase_config("does_not_exist", base_dir=PHASES_FIXTURES_DIR)
