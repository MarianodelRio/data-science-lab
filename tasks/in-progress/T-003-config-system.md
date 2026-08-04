---
id: T-003
phase: 0
agent: infra-agent
depends_on: [T-001]
status: in-progress
folders: ["src/config/", "config/"]
outputs: [settings.yaml, Settings loader, AgentConfig, PhaseConfig, PromptLoader]
size: M
branch: feature/T-003-config-system
pr: ~
---

## Config system + settings.yaml

**Scope:** `src/config/` (loaders) + `config/settings.yaml`. **Shared contract** — defines `AgentConfig`/`PhaseConfig` consumed by node bases and GraphBuilder.

**Delivers:**
- `config/settings.yaml` exactly as in `design.md` § settings.yaml (models, api_keys with `${ENV}` refs, context, workspace, optuna, execution)
- `Settings` loader: parses YAML, resolves `${ENV_VAR}` from environment, raises clear error if a referenced env var is missing
- `AgentConfig` and `PhaseConfig` dataclasses as in `design.md` § Shared contracts
- `PromptLoader.load(agent, version) -> str` reading `config/prompts/{agent}/{version}.md`
- `load_agent_config(name) -> AgentConfig` reading `config/agents/{name}.yaml`
- `load_phase_config(name) -> PhaseConfig` reading `config/phases/{name}.yaml`

**Done when:**
- [ ] `Settings.load()` resolves `${DEEPSEEK_API_KEY}` from env and returns `models.reasoning.provider == "deepseek"`
- [ ] missing env var raises `ConfigError` naming the missing key
- [ ] `PromptLoader.load("x","v1")` returns file contents; missing file raises `FileNotFoundError` with the path
- [ ] `mypy src/config/` passes
- [ ] unit tests cover: env resolution, missing key, prompt loading
- [ ] `docs/configuration.md` documents the settings.yaml schema
