# Configuration Guide

How to change agent, phase, prompt, and model behavior without touching Python code. Updated by
any task that adds a config key or a new provider.

## settings.yaml schema

> Skeleton — populated by the config-loader task (T-003). Full annotated schema of
> `config/settings.yaml` (`models`, `api_keys`, `context`, `workspace`, `optuna`, `execution`
> sections). See `design.md` § settings.yaml structure for the current draft schema.

## Changing a model

> Skeleton — how to edit `config/settings.yaml` → `models.{role}.model` to swap a model for a
> given role, and when a restart is required to pick it up.

## Adding/removing an agent

> Skeleton — the config-mechanics view of adding an agent (agent YAML + prompt file + phase
> registration) and removing one (drop from phase YAML; agent/prompt files become inert but stay
> in git). Complements the procedural steps in `docs/agents.md` § Adding an agent — this section
> explains *why* the mechanism works, that one covers the *how-to checklist*.

## Prompt versioning

> Skeleton — how `config/prompts/{agent}/v1.md`, `v2.md` coexist, how `prompt_version` in the
> agent YAML selects the active version, and the convention for bumping versions vs editing a
> version in place.
