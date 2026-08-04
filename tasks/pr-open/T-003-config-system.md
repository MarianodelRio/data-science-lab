---
id: T-003
phase: 0
agent: infra-agent
depends_on: [T-001]
status: pr-open
folders: ["src/config/", "config/"]
outputs: [settings.yaml, Settings loader, AgentConfig, PhaseConfig, PromptLoader]
size: M
branch: feature/T-003-config-system
pr: "https://github.com/MarianodelRio/data-science-lab/pull/6"
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

## Completed

Implemented the config-loading system exactly per the approved plan: `config/settings.yaml`
(transcribed from `design.md` § settings.yaml structure), `src/config/{paths,errors,schema,
settings,prompts,loaders}.py`, and a public re-export surface in `src/config/__init__.py`.
`AgentConfig`/`PhaseConfig`/`CriticConfig` are frozen dataclasses matching `design.md` § Shared
contracts — `CriticConfig`'s shape was inferred (design.md references but never defines it) and
flagged in `context/decisions.md` for confirmation before T-009 (GraphBuilder) consumes it.
`PyYAML>=6.0` added to `pyproject.toml` (approved out-of-folder scope addition — previously only
available transitively via mlflow).

`Settings.load()` resolves `${ENV_VAR}` references by walking the parsed YAML dict (not raw-text
regex), raising `ConfigError` naming the offending var/field. `PromptLoader.load(agent, version)`
and `load_agent_config`/`load_phase_config` (the latter two taking an optional `base_dir` for test
injection, since `config/agents/`/`config/phases/` don't exist on disk yet) round out the
deliverables. 8 non-obvious decisions logged in `context/decisions.md`.

**Review findings fixed before PR:**
- **Security BLOCKER:** `ApiKeysConfig`/`Settings` leaked all resolved API keys in plaintext via
  the default dataclass `repr`/`str` (e.g. any future `logger.debug(settings)` call). Fixed with
  `field(repr=False)` on the four secret fields (kaggle_username left visible, not secret).
- **Adversarial HIGH (functioned as a second blocker):** malformed or empty `${VAR}` references
  silently passed through unresolved instead of raising — since `.env.example` ships every key
  blank, the default first-run path (`cp .env.example .env`) would silently produce empty API
  keys with no error, defeating the loader's purpose. Fixed with post-substitution validation that
  catches unresolved/malformed references and empty-but-set env vars.
- **Protected-contract hardening** (cheap now vs. costly after T-004/T-009/T-010/T-011 depend on
  the shape): `AgentConfig`/`PhaseConfig`/`CriticConfig` list fields changed to tuples (frozen
  dataclasses were previously unhashable and only shallowly immutable); added an explicit type
  check for a malformed `critic:` block (was leaking a raw `TypeError`); added `encoding="utf-8"`
  to all file reads (previously locale-dependent, reproducibly crashed under `LC_ALL=C`); added a
  path-traversal identifier guard to `load_agent_config`/`load_phase_config`/`PromptLoader.load`
  (unreachable today — no callers outside `src/config/` yet — but cheap to close before one
  exists); added test coverage for the malformed-`settings.yaml` error paths and the falsy-but-
  valid regression cases (`max_tokens: 0`, `interrupt_after: false`, empty lists).

Final state: 57 tests in `src/config` (141 total repo-wide after rebase onto T-002), 98.99%+
coverage, `ruff check`/`ruff format --check`/`mypy src/` all clean. PR: #6.

Deferred to follow-up (flagged by adversarial review as LOW priority, explicitly out of scope for
this pass): full field-level type validation (e.g. rejecting a string where an int is expected),
`WORKSPACE_ROOT` env var has no effect on `workspace.root` in settings.yaml, no bounds-checking of
CLAUDE.md invariants (e.g. `max_parallel_agents`) at the config layer, `IsADirectoryError` vs
`FileNotFoundError` edge case, empty prompt file loads silently.
