"""Loaders for per-agent and per-phase config YAML files.

`base_dir` on both functions defaults to the real `config/agents/` /
`config/phases/` directories and exists purely for test injection — see
context/decisions.md (T-003, decision 5).
"""

from pathlib import Path
from typing import Any

import yaml

from src.config.errors import ConfigError
from src.config.paths import AGENTS_DIR, PHASES_DIR
from src.config.schema import AgentConfig, CriticConfig, PhaseConfig


def _require_field(section: dict[str, Any], key: str, *, file_path: Path) -> Any:
    if key not in section or section[key] is None:
        raise ConfigError(f"Missing required field '{key}' in {file_path}")
    return section[key]


def load_agent_config(name: str, base_dir: str | Path | None = None) -> AgentConfig:
    directory = Path(base_dir) if base_dir is not None else AGENTS_DIR
    file_path = directory / f"{name}.yaml"
    raw_text = file_path.read_text()
    raw = yaml.safe_load(raw_text) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Expected a YAML mapping at the top level of {file_path}")

    return AgentConfig(
        name=_require_field(raw, "name", file_path=file_path),
        model_role=_require_field(raw, "model_role", file_path=file_path),
        prompt_version=_require_field(raw, "prompt_version", file_path=file_path),
        tools=_require_field(raw, "tools", file_path=file_path),
        output_file_pattern=_require_field(raw, "output_file_pattern", file_path=file_path),
        max_tokens=_require_field(raw, "max_tokens", file_path=file_path),
        temperature=raw.get("temperature"),
    )


def _build_critic_config(
    raw_critic: dict[str, Any] | None, *, file_path: Path
) -> CriticConfig | None:
    if raw_critic is None:
        return None
    return CriticConfig(
        node=_require_field(raw_critic, "node", file_path=file_path),
        targets=_require_field(raw_critic, "targets", file_path=file_path),
        max_retries=_require_field(raw_critic, "max_retries", file_path=file_path),
    )


def load_phase_config(name: str, base_dir: str | Path | None = None) -> PhaseConfig:
    directory = Path(base_dir) if base_dir is not None else PHASES_DIR
    file_path = directory / f"{name}.yaml"
    raw_text = file_path.read_text()
    raw = yaml.safe_load(raw_text) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Expected a YAML mapping at the top level of {file_path}")

    return PhaseConfig(
        name=_require_field(raw, "name", file_path=file_path),
        nodes=_require_field(raw, "nodes", file_path=file_path),
        sequence=_require_field(raw, "sequence", file_path=file_path),
        parallel_nodes=_require_field(raw, "parallel_nodes", file_path=file_path),
        critic=_build_critic_config(raw.get("critic"), file_path=file_path),
        interrupt_after=_require_field(raw, "interrupt_after", file_path=file_path),
    )
