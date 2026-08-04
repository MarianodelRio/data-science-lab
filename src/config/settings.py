"""Global settings loader: parses config/settings.yaml and resolves ${ENV_VAR}
references from the environment.

Protected contract (design.md § Shared contracts) — changes require explicit
human approval.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.config.errors import ConfigError
from src.config.paths import SETTINGS_PATH

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Matches anything that still looks like an unresolved/malformed env var reference
# AFTER the main substitution pass has run — e.g. `${HYPHEN-NAME}` (invalid identifier,
# never matched by _ENV_VAR_PATTERN so it survives verbatim) or a bare `$VAR` (no
# braces at all, so _ENV_VAR_PATTERN never touches it either).
_UNRESOLVED_REF_PATTERN = re.compile(r"\$\{|\$[A-Za-z_]")


def _resolve_env_vars(value: Any, *, source: str | Path) -> Any:
    """Recursively resolve ${ENV_VAR} references in a parsed YAML structure."""
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            var_name = m.group(1)
            try:
                resolved = os.environ[var_name]
            except KeyError:
                raise ConfigError(
                    f"Missing required environment variable '{var_name}' referenced in {source}"
                ) from None
            if resolved == "":
                raise ConfigError(
                    f"Environment variable '{var_name}' referenced in {source} is set but empty"
                )
            return resolved

        resolved_string = _ENV_VAR_PATTERN.sub(repl, value)
        if _UNRESOLVED_REF_PATTERN.search(resolved_string):
            raise ConfigError(
                f"Malformed or unresolved environment variable reference in {value!r} "
                f"(source: {source})"
            )
        return resolved_string
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v, source=source) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v, source=source) for v in value]
    return value


def _require_section(raw: dict[str, Any], key: str, *, source: str | Path) -> dict[str, Any]:
    section = raw.get(key)
    if not isinstance(section, dict):
        raise ConfigError(f"Missing required field '{key}' in {source}")
    return section


def _require_field(
    section: dict[str, Any], key: str, *, section_name: str, source: str | Path
) -> Any:
    if key not in section or section[key] is None:
        raise ConfigError(f"Missing required field '{section_name}.{key}' in {source}")
    return section[key]


@dataclass(frozen=True)
class ModelRoleConfig:
    provider: str
    model: str
    temperature: float
    max_tokens: int | None = None  # only "advisor" role sets this in settings.yaml


@dataclass(frozen=True)
class ModelsConfig:
    advisor: ModelRoleConfig
    reasoning: ModelRoleConfig
    implementation: ModelRoleConfig
    research: ModelRoleConfig
    fast: ModelRoleConfig


@dataclass(frozen=True)
class ApiKeysConfig:
    anthropic: str = field(repr=False)
    deepseek: str = field(repr=False)
    groq: str = field(repr=False)
    kaggle_username: str
    kaggle_key: str = field(repr=False)


@dataclass(frozen=True)
class ContextConfig:
    trim_strategy: str
    max_messages_per_node: int


@dataclass(frozen=True)
class WorkspaceConfig:
    root: str
    chroma_host: str
    chroma_port: int
    mlflow_tracking_uri: str


@dataclass(frozen=True)
class OptunaConfig:
    n_trials: int
    early_stopping_patience: int


@dataclass(frozen=True)
class ExecutionConfig:
    max_parallel_agents: int
    code_executor_timeout_seconds: int
    max_critic_retries: int
    max_iterations: int


def _build_model_role_config(
    raw_models: dict[str, Any], role: str, *, source: str | Path
) -> ModelRoleConfig:
    section = _require_section(raw_models, role, source=source)
    return ModelRoleConfig(
        provider=_require_field(section, "provider", section_name=f"models.{role}", source=source),
        model=_require_field(section, "model", section_name=f"models.{role}", source=source),
        temperature=_require_field(
            section, "temperature", section_name=f"models.{role}", source=source
        ),
        max_tokens=section.get("max_tokens"),
    )


@dataclass(frozen=True)
class Settings:
    models: ModelsConfig
    api_keys: ApiKeysConfig
    context: ContextConfig
    workspace: WorkspaceConfig
    optuna: OptunaConfig
    execution: ExecutionConfig

    @classmethod
    def load(cls, path: str | Path = SETTINGS_PATH) -> Settings:
        source = path
        raw_text = Path(path).read_text(encoding="utf-8")
        raw = yaml.safe_load(raw_text) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"Expected a YAML mapping at the top level of {source}")
        resolved = _resolve_env_vars(raw, source=source)

        raw_models = _require_section(resolved, "models", source=source)
        models = ModelsConfig(
            advisor=_build_model_role_config(raw_models, "advisor", source=source),
            reasoning=_build_model_role_config(raw_models, "reasoning", source=source),
            implementation=_build_model_role_config(raw_models, "implementation", source=source),
            research=_build_model_role_config(raw_models, "research", source=source),
            fast=_build_model_role_config(raw_models, "fast", source=source),
        )

        raw_api_keys = _require_section(resolved, "api_keys", source=source)
        api_keys = ApiKeysConfig(
            anthropic=_require_field(
                raw_api_keys, "anthropic", section_name="api_keys", source=source
            ),
            deepseek=_require_field(
                raw_api_keys, "deepseek", section_name="api_keys", source=source
            ),
            groq=_require_field(raw_api_keys, "groq", section_name="api_keys", source=source),
            kaggle_username=_require_field(
                raw_api_keys, "kaggle_username", section_name="api_keys", source=source
            ),
            kaggle_key=_require_field(
                raw_api_keys, "kaggle_key", section_name="api_keys", source=source
            ),
        )

        raw_context = _require_section(resolved, "context", source=source)
        context = ContextConfig(
            trim_strategy=_require_field(
                raw_context, "trim_strategy", section_name="context", source=source
            ),
            max_messages_per_node=_require_field(
                raw_context, "max_messages_per_node", section_name="context", source=source
            ),
        )

        raw_workspace = _require_section(resolved, "workspace", source=source)
        workspace = WorkspaceConfig(
            root=_require_field(raw_workspace, "root", section_name="workspace", source=source),
            chroma_host=_require_field(
                raw_workspace, "chroma_host", section_name="workspace", source=source
            ),
            chroma_port=_require_field(
                raw_workspace, "chroma_port", section_name="workspace", source=source
            ),
            mlflow_tracking_uri=_require_field(
                raw_workspace, "mlflow_tracking_uri", section_name="workspace", source=source
            ),
        )

        raw_optuna = _require_section(resolved, "optuna", source=source)
        optuna = OptunaConfig(
            n_trials=_require_field(raw_optuna, "n_trials", section_name="optuna", source=source),
            early_stopping_patience=_require_field(
                raw_optuna, "early_stopping_patience", section_name="optuna", source=source
            ),
        )

        raw_execution = _require_section(resolved, "execution", source=source)
        execution = ExecutionConfig(
            max_parallel_agents=_require_field(
                raw_execution, "max_parallel_agents", section_name="execution", source=source
            ),
            code_executor_timeout_seconds=_require_field(
                raw_execution,
                "code_executor_timeout_seconds",
                section_name="execution",
                source=source,
            ),
            max_critic_retries=_require_field(
                raw_execution, "max_critic_retries", section_name="execution", source=source
            ),
            max_iterations=_require_field(
                raw_execution, "max_iterations", section_name="execution", source=source
            ),
        )

        return cls(
            models=models,
            api_keys=api_keys,
            context=context,
            workspace=workspace,
            optuna=optuna,
            execution=execution,
        )
