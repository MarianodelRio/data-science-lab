"""Shared config dataclasses consumed by node bases and GraphBuilder.

Protected contract (design.md § Shared contracts) — changes require explicit
human approval.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CriticConfig:
    node: str
    targets: list[str]
    max_retries: int


@dataclass(frozen=True)
class AgentConfig:
    name: str
    model_role: str
    prompt_version: str
    tools: list[str]
    output_file_pattern: str
    max_tokens: int
    temperature: float | None = None


@dataclass(frozen=True)
class PhaseConfig:
    name: str
    nodes: list[str]
    sequence: list[str]
    parallel_nodes: list[str]
    critic: CriticConfig | None
    interrupt_after: bool
