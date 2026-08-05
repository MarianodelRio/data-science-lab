"""LLMFactory: sole point of LLM instantiation for the pipeline.

Protected contract (design.md § Shared contracts) — the `LLMFactory.get` signature
requires explicit human approval to change.

`LLMFactory.get(role)` resolves a `models.{role}` entry from `Settings` (loaded once
and cached at class level) and dispatches to the matching LangChain chat model class
for that role's configured provider.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.config.settings import ModelRoleConfig, ModelsConfig, Settings

_ROLE_RESOLVERS: dict[str, Callable[[ModelsConfig], ModelRoleConfig]] = {
    "advisor": lambda models: models.advisor,
    "reasoning": lambda models: models.reasoning,
    "implementation": lambda models: models.implementation,
    "research": lambda models: models.research,
    "fast": lambda models: models.fast,
}


def _base_kwargs(role_config: ModelRoleConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"model": role_config.model, "temperature": role_config.temperature}
    if role_config.max_tokens is not None:
        kwargs["max_tokens"] = role_config.max_tokens
    return kwargs


def _build_anthropic(role_config: ModelRoleConfig, settings: Settings) -> BaseChatModel:
    return ChatAnthropic(
        **_base_kwargs(role_config), api_key=SecretStr(settings.api_keys.anthropic)
    )


def _build_deepseek(role_config: ModelRoleConfig, settings: Settings) -> BaseChatModel:
    return ChatOpenAI(
        **_base_kwargs(role_config),
        api_key=SecretStr(settings.api_keys.deepseek),
        base_url="https://api.deepseek.com",
    )


def _build_groq(role_config: ModelRoleConfig, settings: Settings) -> BaseChatModel:
    return ChatGroq(**_base_kwargs(role_config), api_key=SecretStr(settings.api_keys.groq))


def _build_openai(role_config: ModelRoleConfig, settings: Settings) -> BaseChatModel:
    # `ApiKeysConfig` has no `openai` field (settings.yaml only carries keys for
    # providers actually used by config/settings.yaml today) — rely on ChatOpenAI's
    # own OPENAI_API_KEY env-var fallback. See context/discoveries.md.
    return ChatOpenAI(**_base_kwargs(role_config))


def _build_gemini(role_config: ModelRoleConfig, settings: Settings) -> BaseChatModel:
    # Same gap as `_build_openai`: no `settings.api_keys.gemini` field — rely on
    # ChatGoogleGenerativeAI's own GOOGLE_API_KEY env-var fallback.
    return ChatGoogleGenerativeAI(**_base_kwargs(role_config))


_PROVIDER_BUILDERS: dict[str, Callable[[ModelRoleConfig, Settings], BaseChatModel]] = {
    "anthropic": _build_anthropic,
    "deepseek": _build_deepseek,
    "groq": _build_groq,
    "openai": _build_openai,
    "gemini": _build_gemini,
}


class LLMFactory:
    """Sole point of LLM instantiation. See design.md § Shared contracts."""

    _settings: ClassVar[Settings | None] = None

    @classmethod
    def get(cls, role: str) -> BaseChatModel:
        """Return a configured LangChain chat model for the given `models.{role}`.

        Raises:
            KeyError: `role` is not one of the five fixed roles in `ModelsConfig`.
            ValueError: the role's configured provider is not a recognized dispatch key.
        """
        if cls._settings is None:
            cls._settings = Settings.load()
        settings = cls._settings

        try:
            resolve_role = _ROLE_RESOLVERS[role]
        except KeyError:
            raise KeyError(role) from None
        role_config = resolve_role(settings.models)

        try:
            build = _PROVIDER_BUILDERS[role_config.provider]
        except KeyError:
            raise ValueError(f"Unknown provider: {role_config.provider!r}") from None
        return build(role_config, settings)
