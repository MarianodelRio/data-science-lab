"""Public re-export surface for the config package.

Protected contract (design.md § Shared contracts) — changes require explicit
human approval.
"""

from src.config.errors import ConfigError
from src.config.loaders import load_agent_config, load_phase_config
from src.config.prompts import PromptLoader
from src.config.schema import AgentConfig, CriticConfig, PhaseConfig
from src.config.settings import Settings

__all__ = [
    "AgentConfig",
    "ConfigError",
    "CriticConfig",
    "PhaseConfig",
    "PromptLoader",
    "Settings",
    "load_agent_config",
    "load_phase_config",
]
