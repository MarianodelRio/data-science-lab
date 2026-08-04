"""Loaders for per-agent and per-phase config YAML files.

`base_dir` on both functions defaults to the real `config/agents/` /
`config/phases/` directories and exists purely for test injection — see
context/decisions.md (T-003, decision 5).
"""

import re
from pathlib import Path
from typing import Any

import yaml

from src.config.errors import ConfigError
from src.config.paths import AGENTS_DIR, PHASES_DIR
from src.config.schema import AgentConfig, CriticConfig, PhaseConfig

_SAFE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")


def validate_identifier(value: str, *, label: str) -> None:
    """Reject identifiers used to build filesystem paths that could escape the
    intended config directory (path traversal via `/`, `\\`, or `..`).

    The character whitelist alone permits a bare `".."` (every character in it
    is allowed), so `..` is rejected explicitly too — this matters for callers
    like `PromptLoader.load` where the identifier is used as a raw path segment
    rather than suffixed with an extension.
    """
    if not _SAFE_IDENTIFIER_PATTERN.fullmatch(value) or ".." in value:
        raise ConfigError(f"Invalid {label} '{value}'")


def _require_field(section: dict[str, Any], key: str, *, file_path: Path) -> Any:
    if key not in section or section[key] is None:
        raise ConfigError(f"Missing required field '{key}' in {file_path}")
    return section[key]


def load_agent_config(name: str, base_dir: str | Path | None = None) -> AgentConfig:
    validate_identifier(name, label="agent name")
    directory = Path(base_dir) if base_dir is not None else AGENTS_DIR
    file_path = directory / f"{name}.yaml"
    raw_text = file_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Expected a YAML mapping at the top level of {file_path}")

    return AgentConfig(
        name=_require_field(raw, "name", file_path=file_path),
        model_role=_require_field(raw, "model_role", file_path=file_path),
        prompt_version=_require_field(raw, "prompt_version", file_path=file_path),
        tools=tuple(_require_field(raw, "tools", file_path=file_path)),
        output_file_pattern=_require_field(raw, "output_file_pattern", file_path=file_path),
        max_tokens=_require_field(raw, "max_tokens", file_path=file_path),
        temperature=raw.get("temperature"),
    )


def _build_critic_config(raw_critic: Any, *, file_path: Path) -> CriticConfig | None:
    if raw_critic is None:
        return None
    if not isinstance(raw_critic, dict):
        raise ConfigError(f"Field 'critic' must be a mapping in {file_path}")
    return CriticConfig(
        node=_require_field(raw_critic, "node", file_path=file_path),
        targets=tuple(_require_field(raw_critic, "targets", file_path=file_path)),
        max_retries=_require_field(raw_critic, "max_retries", file_path=file_path),
    )


def load_phase_config(name: str, base_dir: str | Path | None = None) -> PhaseConfig:
    validate_identifier(name, label="phase name")
    directory = Path(base_dir) if base_dir is not None else PHASES_DIR
    file_path = directory / f"{name}.yaml"
    raw_text = file_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(raw_text) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"Expected a YAML mapping at the top level of {file_path}")

    return PhaseConfig(
        name=_require_field(raw, "name", file_path=file_path),
        nodes=tuple(_require_field(raw, "nodes", file_path=file_path)),
        sequence=tuple(_require_field(raw, "sequence", file_path=file_path)),
        parallel_nodes=tuple(_require_field(raw, "parallel_nodes", file_path=file_path)),
        critic=_build_critic_config(raw.get("critic"), file_path=file_path),
        interrupt_after=_require_field(raw, "interrupt_after", file_path=file_path),
    )
