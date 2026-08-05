# Configuration Guide

How to change agent, phase, prompt, and model behavior without touching Python code. Updated by
any task that adds a config key or a new provider.

## settings.yaml schema

`config/settings.yaml` is the single global config file: model assignments, API key references,
and execution parameters. It is loaded via `Settings.load()` (`src/config/settings.py`), which
parses the YAML, resolves any `${ENV_VAR}` references against the process environment, and
returns a frozen `Settings` dataclass. There are six top-level sections.

### `models`

Five fixed roles, each with `provider`, `model`, `temperature`, and an optional `max_tokens`
(only `advisor` sets it in the current file — the others fall back to `None` and pick up the
provider default). `ModelsConfig` is a fixed-attribute dataclass (not `dict[str, ModelRoleConfig]`)
so a role is read via plain attribute access, e.g. `settings.models.reasoning.provider`.

| Role | Provider / model | Used by |
|---|---|---|
| `advisor` | Anthropic Claude Opus 5 | high-risk architecture decisions (Advisor agent) |
| `reasoning` | DeepSeek V4 Flash | `solution_architect`, `feature_engineer`, `hypothesis_generator`, `experiment_designer` |
| `implementation` | DeepSeek V4 Flash | `coder`, `code_critic`, `baseline_designer` |
| `research` | DeepSeek V3.2 | `literature_researcher`, `web_researcher`, `competition_analyst`, `report_writer` |
| `fast` | Groq Llama 4 Maverick (free tier) | `analysis_critic`, `memory_manager`, `problem_framer`, `validation_strategist` |

| Key | Type | Meaning | Example |
|---|---|---|---|
| `models.{role}.provider` | `str` | Provider dispatch key consumed by `LLMFactory` (`anthropic`, `deepseek`, `groq`, ...) | `deepseek` |
| `models.{role}.model` | `str` | Provider-specific model identifier | `deepseek-v4-flash` |
| `models.{role}.temperature` | `float` | Sampling temperature for that role | `0.5` |
| `models.{role}.max_tokens` | `int \| null` | Optional per-role output cap; `null`/absent → `ModelRoleConfig.max_tokens is None` | `4096` |

### `api_keys`

Every value is a `${ENV_VAR}` reference, resolved at `Settings.load()` time by walking the
*parsed* YAML dict (not the raw text) and substituting `os.environ[VAR]`. If a referenced env var
is not set, `Settings.load()` raises `ConfigError` naming the missing variable and the source
file path — it never lets a bare `KeyError` escape. See `.env.example` at the repo root for the
full list of variables the running system expects (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`,
`GROQ_API_KEY`, `KAGGLE_USERNAME`, `KAGGLE_KEY`).

| Key | Type | Meaning | Example |
|---|---|---|---|
| `api_keys.anthropic` | `str` (`${ENV}`) | Anthropic API key | `${ANTHROPIC_API_KEY}` |
| `api_keys.deepseek` | `str` (`${ENV}`) | DeepSeek API key | `${DEEPSEEK_API_KEY}` |
| `api_keys.groq` | `str` (`${ENV}`) | Groq API key | `${GROQ_API_KEY}` |
| `api_keys.kaggle_username` | `str` (`${ENV}`) | Kaggle account username | `${KAGGLE_USERNAME}` |
| `api_keys.kaggle_key` | `str` (`${ENV}`) | Kaggle API key | `${KAGGLE_KEY}` |

### `context`

| Key | Type | Meaning | Example |
|---|---|---|---|
| `context.trim_strategy` | `str` | How message history is trimmed before it reaches a node | `last_n_messages` |
| `context.max_messages_per_node` | `int` | Cap applied by that trim strategy | `10` |

### `workspace`

| Key | Type | Meaning | Example |
|---|---|---|---|
| `workspace.root` | `str` | Workspace root path as seen *inside* the Docker container (host path lives in `docker-compose.yml`) | `/competitions` |
| `workspace.chroma_host` | `str` | ChromaDB server hostname | `chroma` |
| `workspace.chroma_port` | `int` | ChromaDB server port | `8000` |
| `workspace.mlflow_tracking_uri` | `str` | MLflow tracking server URI | `http://mlflow:5000` |

### `optuna`

| Key | Type | Meaning | Example |
|---|---|---|---|
| `optuna.n_trials` | `int` | Max Optuna trials per tuning run | `50` |
| `optuna.early_stopping_patience` | `int` | Trials without improvement before early stop | `20` |

### `execution`

| Key | Type | Meaning | Example |
|---|---|---|---|
| `execution.max_parallel_agents` | `int` | Concurrency cap for Pipeline Phase 2 (research) | `2` |
| `execution.code_executor_timeout_seconds` | `int` | Subprocess timeout for the code executor tool | `3600` |
| `execution.max_critic_retries` | `int` | Retries before a critic is force-passed | `3` |
| `execution.max_iterations` | `int` | Max Phase 4→6 design/evaluation loops | `10` |

## Changing a model

Edit `models.{role}.model` (and optionally `temperature` / `max_tokens`) in
`config/settings.yaml`. Every agent whose `AgentConfig.model_role` matches that role picks up the
change automatically — no code change needed.

`Settings.load()` itself re-parses and re-resolves the file on every call — no caching at that
layer. `LLMFactory` (`src/llm/factory.py`, T-004) is the actual long-lived consumer: it calls
`Settings.load()` once, on the first `LLMFactory.get(role)` in the process, and caches the
resulting `Settings` instance on the class for every subsequent `.get()` call. So a **running
process still needs a restart** to pick up a model change, even though `Settings.load()` itself is
always fresh.

`LLMFactory.get(role)` resolves `role` (`"advisor"`, `"reasoning"`, `"implementation"`,
`"research"`, or `"fast"`) to a `ModelRoleConfig` via an explicit mapping — an unrecognized role
raises `KeyError(role)`, never `AttributeError`. It then dispatches `role_config.provider` to the
matching LangChain chat model class, also via an explicit mapping:

| `provider` | LangChain class | Notes |
|---|---|---|
| `anthropic` | `ChatAnthropic` | `api_key=settings.api_keys.anthropic` |
| `deepseek` | `ChatOpenAI` | `base_url="https://api.deepseek.com"`, `api_key=settings.api_keys.deepseek` |
| `groq` | `ChatGroq` | `api_key=settings.api_keys.groq` |
| `openai` | `ChatOpenAI` | no `settings.api_keys.openai` field — relies on `ChatOpenAI`'s own `OPENAI_API_KEY` env fallback |
| `gemini` | `ChatGoogleGenerativeAI` | no `settings.api_keys.gemini` field — relies on `ChatGoogleGenerativeAI`'s own `GOOGLE_API_KEY` env fallback |

An unrecognized `provider` string raises `ValueError(f"Unknown provider: {provider!r}")`. Every
wrapper passes `model` and `temperature` from the resolved `ModelRoleConfig`, and adds
`max_tokens` only when it is not `None` (today, only the `advisor` role sets it).

Before:
```yaml
reasoning:
  provider: deepseek
  model: deepseek-v4-flash
  temperature: 0.5
```

After (swap provider and model for the `reasoning` role):
```yaml
reasoning:
  provider: anthropic
  model: claude-sonnet-5
  temperature: 0.5
```

## Adding/removing an agent

This section covers the config mechanics; `docs/agents.md` § Adding an agent has the full
how-to checklist (files to create, in what order). Here's *why* each piece exists:

- `AgentConfig.model_role` selects which `models.{role}` entry in `settings.yaml` the agent runs
  under (via `LLMFactory`) — it is a role name, not a raw provider/model pair, so multiple agents
  can share one model assignment.
- `AgentConfig.prompt_version` selects which `config/prompts/{agent}/{version}.md` file
  `PromptLoader.load(agent, version)` returns — it does not select a model.
- `AgentConfig.output_file_pattern` is a template string (e.g.
  `"design/iteration_{iteration}/solution_plan.json"`). `load_agent_config` treats it as an opaque
  string; interpolation and file writing are the responsibility of the node and
  `WorkspaceManager`, not the config loader.
- `AgentConfig.tools` and `.max_tokens` are passed through as-is for the node/LLM call site to
  interpret.

`load_agent_config(name)` reads `config/agents/{name}.yaml`. A missing file raises the stdlib
`FileNotFoundError` (uncaught, path included in its message); a present file missing a required
field raises `ConfigError("Missing required field '{field}' in {file_path}")`.

Removing an agent from a phase YAML's `nodes`/`sequence` lists is enough to take it out of the
graph — its `config/agents/{name}.yaml` and `config/prompts/{name}/` files are not deleted and
become inert (harmless to leave in git; nothing loads them until the agent is re-registered in a
phase).

## Prompt versioning

`PromptLoader.load(agent, version)` reads `config/prompts/{agent}/{version}.md` and returns its
contents as-is (no templating at this layer). `prompt_version` in the agent's YAML selects which
version is currently active; multiple versions (`v1.md`, `v2.md`, ...) can coexist in the same
directory for A/B testing — nothing deletes an old version when a new one is added.

**Never edit a shipped version file in place.** Past experiment runs recorded which
`prompt_version` they used (in MLflow / the workspace); editing `v1.md` after the fact silently
changes the historical meaning of every run that cited it. To change a prompt, create a new
`vN.md` and bump `prompt_version` in the agent YAML — old versions stay in git history and on
disk, readable by anything that still references them.

A missing prompt file raises the stdlib `FileNotFoundError` from `Path.read_text()` — unwrapped —
so its message already contains the path that was looked up.
