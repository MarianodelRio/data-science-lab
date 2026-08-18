# Data Science Lab — Specification

> **Brownfield spec.** Generated from the existing codebase (33 tasks merged), not
> from an ideal design. It documents **what the code actually does today**. Where
> `design.md` and the implementation disagree, this file follows the implementation
> and says so.
>
> Companion documents: `design.md` (intended architecture), `plan.md` (task graph),
> `docs/pipeline.md` (node-level reference). Edit this file only via `/refine`.

**Stack:** Python ≥3.10 · LangGraph + LangChain · ChromaDB · MLflow · Optuna ·
FastAPI · React 19 + Vite + TypeScript. Tests: pytest (45 test modules) +
Vitest. Lint/type: ruff + mypy.

**Implementation status:** infrastructure and Pipeline Phases 1–6 (compute side)
are landed; 7 graph nodes, the whole FastAPI backend, 5 frontend components,
Docker and CI are not yet implemented — each is called out under
*Out of scope (not yet built)* in its section.

---

## LabState — pipeline state contract

**What it does** — Defines `LabState`, the single `TypedDict` threaded through every
node of the LangGraph graph, plus `new_state()` which builds a fresh instance for a run.

**Logic**
- Type-only module: no I/O, no LLM calls, no side effects.
- State holds **pointers, scalars and control flags only**. Large artifacts (EDA
  reports, experiment results, generated code) live on disk in the workspace; the
  state carries their paths.
- `best_score` is initialised to `float("-inf")` so the first real experiment always
  counts as an improvement; every other score field starts at `0.0`.
- `max_iterations` defaults to `10`, mirroring `execution.max_iterations` in
  `config/settings.yaml`. The two are not linked in code — they are kept in sync by hand.
- `messages` is `Annotated[list, add_messages]` — the only reducer-managed field;
  every other field is last-write-wins.
- **There is no score-polarity field.** Metric direction is resolved downstream in
  `score_evaluator`, not carried on the state.

**Interface**
- Inputs: `competition_name`, `workspace_path`, optional `max_iterations`.
- Outputs: a `LabState` dict. Fields: file pointers (`eda_report_path`,
  `problem_definition_path`, `validation_config_path`, `baseline_results_path`,
  `solution_plan_path`, `feature_spec_path`), control (`phase`, `current_iteration`,
  `max_iterations`, `iterations_without_improvement`), scores (`baseline_score`,
  `best_score`, `last_score`, `score_delta`), `experiments` (list of
  `{id, path, cv_score, iteration, model}`), `best_experiment_path`,
  `checkpoint_summary`, `human_feedback`, `messages`.
- Errors: none — it raises nothing.

**Out of scope** — validation, persistence, defaults beyond the zero values.

**Protected contract.** Changing `LabState` requires explicit human approval
(CLAUDE.md, `design.md` § Shared contracts).

---

## Configuration (`src/config/`)

**What it does** — Loads and validates all external configuration: global settings
(`config/settings.yaml`), per-agent YAML, per-phase YAML, and agent prompt files.

**Logic**
- `Settings.load()` parses `settings.yaml` and **recursively resolves `${ENV_VAR}`
  references**; a missing env var raises `ConfigError`, it never silently yields an
  empty string.
- `load_agent_config(name)` / `load_phase_config(name)` read
  `config/agents/{name}.yaml` and `config/phases/{name}.yaml` into the frozen
  dataclasses `AgentConfig` / `PhaseConfig` (+ nested `CriticConfig`). Both take an
  optional `base_dir` used **only for test injection**.
- `validate_identifier()` rejects any name that could escape the config directory
  when interpolated into a path (traversal guard) — applied before a config name
  ever touches the filesystem.
- Every missing required field raises `ConfigError` naming the file — no defaulting.
- `PromptLoader.load(agent, version)` reads `config/prompts/{agent}/{version}.md`.
  Prompts are never inline in Python (CLAUDE.md invariant #7).
- `paths.py` holds the canonical repo-root-relative locations; every other module
  derives paths from it rather than recomputing them.

**Interface**
- Inputs: `config/settings.yaml`, `config/agents/*.yaml` (18 files),
  `config/phases/*.yaml` (7 files), `config/prompts/{agent}/v1.md` (18 files),
  environment variables for API keys.
- Outputs: `Settings`, `AgentConfig`, `PhaseConfig`, `CriticConfig` (all frozen
  dataclasses), prompt strings.
- Errors: `ConfigError` for a missing env var, missing file, missing field, or an
  identifier that fails the traversal guard.

**Out of scope** — writing config, hot-reload, schema migration between versions.

**Protected contract.** `src/config/` dataclasses, `config/settings.yaml` and
`config/phases/*.yaml` require explicit human approval to change.

---

## LLM factory (`src/llm/factory.py`)

**What it does** — The sole point of LLM instantiation. `LLMFactory.get(role)` maps a
role name to a configured provider and returns a LangChain `BaseChatModel`.

**Logic**
- Five roles — `advisor`, `reasoning`, `implementation`, `research`, `fast` — resolved
  through a role→resolver table against `ModelsConfig`.
- Five providers supported: Anthropic, DeepSeek, Groq, OpenAI, Gemini. DeepSeek and
  OpenAI both go through `ChatOpenAI` (DeepSeek with its own base URL).
- `Settings` is loaded once and cached at class level, so config is read from disk
  once per process regardless of how many nodes ask for a model.
- API keys are wrapped in `pydantic.SecretStr` — never passed as plain `str`.
- Model routing lives entirely in `settings.yaml`; changing a model is a config edit,
  never a code edit.

**Interface**
- Inputs: role name (`str`); `config/settings.yaml` `models.{role}` section; provider
  API keys from the environment.
- Outputs: a configured `BaseChatModel`.
- Errors: `ConfigError` for an unknown role, unknown provider, or a missing API key.

**Out of scope** — prompt construction, retries, streaming, cost accounting.

**Protected contract.** The `LLMFactory.get` signature requires explicit human approval.

---

## WorkspaceManager (`src/workspace/workspace_manager.py`)

**What it does** — The **sole file-I/O point** to the generated ML workspace
(`~/competitions/{name}/`). Every node and tool that touches workspace files goes
through it (CLAUDE.md invariant #2).

**Logic**
- Every `relative_path` is resolved against the workspace root and **rejected**
  (`ValueError`) if it is absolute, empty, `.`, or contains any `..` traversal
  component. This is the containment guarantee the whole invariant rests on.
- Writes are **atomic**: content goes to a uuid-suffixed temp file in the target
  directory, then `os.replace()`. A crash mid-write can never leave a partial file.
- Parent directories are created on demand.
- **Asymmetric path convention (load-bearing):** `write_json`/`write_text`/
  `write_notebook` return an **absolute** path, while `read_json`/`read_text` require
  a **relative** one. Nodes that store a write result into `LabState` and later read
  it back must re-relativize first — `src/nodes/llm/base.relative_to_workspace()`
  exists for exactly this.
- `write_notebook` builds a real `nbformat` v4 notebook from a cell list (markdown
  and code cells).
- `experiment_dir(exp_id)` and `ensure_dir(relative_path)` create and return directories.

**Interface**
- Inputs: workspace root at construction; workspace-relative paths on every method.
- Outputs: parsed JSON / text on read; absolute written path on write.
- Errors: `ValueError` for an unsafe or malformed relative path; `OSError` propagates
  from the filesystem; `json.JSONDecodeError` propagates from `read_json`.

**Out of scope** — schema validation of file contents, git operations, remote storage,
concurrency locking between processes.

**Protected contract.** The `WorkspaceManager` public API requires explicit human approval.

---

## RAG memory (`src/memory/store.py` + `src/tools/rag.py`)

**What it does** — Persistent per-competition knowledge store: one ChromaDB collection
per competition, local sentence-transformer embeddings, metadata-filtered retrieval.
`memory/store.py` is the low-level Chroma wrapper; `tools/rag.py` is the tool-facing
`RagStore` API used by nodes.

**Logic**
- **Collection naming is hash-suffixed, not merely sanitized.** `sanitize_collection_name`
  appends a hash of the raw competition name to the cleaned prefix, capped at Chroma's
  63-char limit. A purely lossy text transform is not injective — two different
  competitions could otherwise collide onto one collection and leak documents across
  tenants. The hash is what makes collisions impossible.
- Embeddings are computed locally with `all-MiniLM-L6-v2` — no embedding API calls, no cost.
- **No LLM calls anywhere in either module** (CLAUDE.md invariant #8). Structured
  metadata extraction is the *caller's* job: research nodes build `IndexDocument`
  objects themselves and hand them to `RagStore.index()`.
- Chroma metadata values must be scalars, so the list-valued fields
  (`problem_type`, `methods_used`, `dataset_characteristics`) are flattened on write
  and the corresponding `where` filters are translated on read (`translate_where`,
  `_legalize_where`) so callers can keep querying them as lists.

**Interface**
- Inputs: `IndexDocument` objects (content + structured metadata); query text +
  optional metadata filter + `n_results`; Chroma host/port from settings.
- Outputs: ranked documents with metadata and distances.
- Errors: Chroma client/connection errors propagate.

**Out of scope** — deciding *what* is worth remembering (that is `memory_manager`'s
job), summarization, cross-competition retrieval (deliberately impossible by design).

**Protected contract.** The `RagStore` / `IndexDocument` schema requires explicit
human approval.

### Untested behavior
> **WARNING: no dedicated test module for `src/memory/store.py`.** It is exercised
> only indirectly through `tests/tools/test_rag.py`. The behavior below is inferred
> from source and not directly verified:
> - hash-suffix truncation at exactly the 63-char boundary
> - `build_client` host/port fallback when Chroma is unreachable
> - embedding-function construction (model download path, offline behavior)

---

## Code executor (`src/tools/code_executor.py`)

**What it does** — Runs LLM-generated Python in a **subprocess** and returns a
structured result. Used by `data_analyst` and `validation_strategist` — the pipeline
never calls `exec`/`eval` inline.

**Logic**
- `execute()` **never raises.** A missing or invalid `cwd`, a nonzero exit code, and a
  timeout all come back as a well-formed `ExecResult`.
- The child runs in its **own process group**; on timeout the whole group is signalled,
  so code that spawns children cannot outlive its own timeout.
- The child environment is built explicitly rather than inherited wholesale.
- Timeout defaults to `execution.code_executor_timeout_seconds` (3600) from settings.

**Interface**
- Inputs: `code: str`, `cwd: str`, optional `timeout: int`.
- Outputs: `ExecResult` (exit code, stdout, stderr, timed-out flag).
- Errors: none raised — all failure modes are encoded in the return value.

**Out of scope** — sandboxing/isolation beyond the process group (no container, no
seccomp, no resource caps), dependency installation, output parsing.

---

## Kaggle client (`src/tools/kaggle_client.py`)

**What it does** — Thin wrapper over the `kaggle` package: download competition data,
submit predictions, read back the latest score, list top public kernels.

**Logic**
- **`kaggle` is never imported at module scope.** The package authenticates eagerly on
  import and raises `OSError` in any credential-less environment (e.g. CI). So
  `_default_api()` checks `KAGGLE_USERNAME`/`KAGGLE_KEY` itself first via
  `_require_env` and only then imports `kaggle` — importing this module is always safe,
  and the failure is this module's `RuntimeError` naming the missing variable rather
  than `kaggle`'s opaque `OSError`.
- `_validate_competition()` guards the competition slug before it is interpolated anywhere.
- The API object is injectable (`KaggleApiProtocol`) — this is what makes the module
  unit-testable with no network.

**Interface**
- Inputs: competition slug, destination dir / submission file / message;
  `KAGGLE_USERNAME`, `KAGGLE_KEY`.
- Outputs: downloaded file list, submission result, latest score dict, kernel list.
- Errors: `RuntimeError` for missing credentials or an invalid slug; Kaggle API errors
  propagate.

**Out of scope** — rate limiting, retry/backoff, leaderboard scraping, submission
scheduling.

---

## Observability (`src/observability/jsonl_callback.py`)

**What it does** — Layer 1 observability (always on): a LangChain
`BaseCallbackHandler` that appends one JSON line per node/LLM event to
`runs/{run_id}/execution.jsonl`.

**Logic**
- **Logging never raises into the pipeline.** Every public hook wraps its
  implementation and reports a one-line warning on stderr instead of propagating.
- `_validate_run_id` guards the run id before it becomes a directory name.
- `_is_real_node_event` filters LangGraph's internal chain events so the log records
  actual pipeline nodes, not framework plumbing.
- Phase is extracted from event metadata with a fallback to the state inputs.
- Token counts are summed defensively (`_sum_optional`) — a provider that omits usage
  yields `None`, not a wrong zero.
- Does **not** import from `src/graph/` — `RUNS_DIR` is derived independently from
  `src.config.paths.REPO_ROOT`, keeping the observability layer free of a graph dependency.
- `runs_dir` is injectable for tests, mirroring `build_checkpointer`.

**Interface**
- Inputs: LangChain callback events; `run_id`; optional `runs_dir`.
- Outputs: append-only `runs/{run_id}/execution.jsonl`.
- Errors: none propagated.

**Known gap (documented, unfixed):** `_summarize_output` writes the last LLM message's
content into `output_summary` near-verbatim — 200-char truncated and
whitespace-collapsed, but **not redacted**. Safe today because every message
originates from this repo's own prompts, but it is not a redaction boundary. See
`context/decisions/T-012.md`.

**Out of scope** — MLflow tracking (layer 2, in `baseline_runner`), LangSmith (layer 3),
log rotation, metrics aggregation.

---

## Graph assembly (`src/graph/`)

**What it does** — Assembles the seven phase subgraphs into the single top-level
LangGraph `StateGraph`, wires the supervisor's conditional edges, resolves node
modules by convention, and attaches the SQLite checkpointer.

**Logic**
- **Node discovery is by convention, with no central registry.** `resolve_node(name)`
  imports `src/nodes/{llm|compute}/{name}.py` and finds the single class *defined in
  that module* whose plain class attribute `name` equals the module stem. This is why
  parallel node tasks never conflict over a shared file.
- `name` **must be a plain class attribute.** A Pydantic v2 typed field
  (`name: str = "..."`) is invisible to `getattr` on the class, so such a node would
  fail discovery — loudly, with `GraphBuilderError`.
- **A missing node module is not an error.** `resolve_node` falls back to `NoOpNode`,
  which returns `{}` and never touches state. This is what lets the full 7-phase graph
  compile today while 7 nodes are still unimplemented. A module that *exists* but is
  broken (ambiguous class, missing class, bad transitive import) raises
  `GraphBuilderError` and fails the whole build.
- Phase subgraph wiring lives **once** in `phases/generic.py`, driven entirely by
  `PhaseConfig`; the seven `phase{N}_{name}.py` modules are thin wrappers that load
  their own YAML and delegate.
- `supervisor()` is the **only** conditional routing logic in the graph: pure Python,
  deterministic, no LLM. It is called from exactly two edges — out of `phase2_research`
  and out of `phase6_evaluation` — and `GraphBuilder` restricts the legal branches at
  each call site with `path_map`.
  - after `phase2_research` → `phase3_baseline` **only** when `current_iteration == 0`
    (CLAUDE.md invariant #4), otherwise `phase4_design`
  - after `phase6_evaluation` → `phase7_delivery` when
    `iterations_without_improvement >= max_iterations`, otherwise loop to `phase4_design`
  - any other phase → `GraphBuilderError`
- Critic retries and specialist dispatch are deliberately **not** supervisor concerns —
  they stay internal to their owning nodes (`context/decisions/T-009.md`).
- `build_checkpointer` uses `SqliteSaver(sqlite3.connect(...))` rather than
  `SqliteSaver.from_conn_string` (which is a context manager in the installed version
  and would close the connection before the compiled graph is used).
- The human checkpoints are `interrupt_after` flags on phases 1, 4 and 6, and are
  **forward-only**: no interrupt re-runs a completed phase, and `human_feedback` is
  read-only advisory context that never changes routing
  (`context/decisions/general.md`).

**Interface**
- Inputs: `config/phases/*.yaml`; node modules under `src/nodes/`; `run_id`; optional
  `runs_dir`.
- Outputs: a `CompiledStateGraph` with checkpointer attached; resume via
  `graph.invoke(None, config={"thread_id": run_id})`.
- Errors: `GraphBuilderError` for an ambiguous/missing node class in a landed module or
  an unexpected supervisor phase; `ConfigError` from phase-config loading.

**Out of scope** — running the graph (the API layer's job), retry policy, distributed
execution, streaming events out.

---

## Node base classes (`src/nodes/llm/base.py`, `src/nodes/compute/base.py`)

**What it does** — The two base classes every pipeline node subclasses, and the split
that keeps compute nodes free of LLM dependencies.

**Logic**
- Both are constructible with **zero arguments** — `resolve_node` calls `cls()`.
- `LLMNode` is a plain class, **not** a Pydantic model, so its `name` stays visible to
  `getattr`.
- `LLMNode.__call__` is a template method: load `AgentConfig` → load prompt via
  `PromptLoader` → get model via `LLMFactory` → `_build_messages` → invoke →
  `_write_output` → `_build_output_state`. Subclasses override the hooks, not `__call__`
  (the two critics are the deliberate exception — they override `__call__` wholesale).
- `trim_context` keeps the last N messages (`context.max_messages_per_node`).
  `n <= 0` is special-cased to return `[]` — `messages[-0:]` returns the *whole* list
  in Python, which would be the opposite of the intent.
- `relative_to_workspace` bridges the WorkspaceManager path asymmetry (absolute out of
  writes, relative into reads); already-relative input passes through unchanged.
- `ComputeNode` is an ABC with an abstract `run`, so a compute node that forgets to
  implement it fails loudly at `cls()` inside `resolve_node` rather than silently
  no-op'ing. It has no model, no config, no prompt, and **never imports from `src/llm`
  or any langchain package** (CLAUDE.md invariant #8, enforced by a test).
- `ComputeNode.workspace(state)` builds a fresh `WorkspaceManager` per call — nodes are
  constructed argument-free, so there is nowhere to cache one.
- Private helper modules (`_experiment_design.py`, `_research_common.py`,
  `_evaluation_common.py`) deliberately declare **no** class matching their own stem, so
  node discovery never mistakes them for nodes.

**Interface**
- Inputs: `LabState`; agent YAML; prompt file.
- Outputs: a partial-state `dict` (LangGraph merges it).
- Errors: `ConfigError` from config/prompt loading; `ValueError` from output validation;
  node-specific errors below.

**Out of scope** — retry logic, cost tracking, tool calling (nodes that need tools call
them directly).

---

## Pipeline Phase 1 — Understanding

Nodes: `data_analyst` → `problem_framer` → `validation_strategist` → `leakage_auditor`
→ `analysis_critic`. Sequential, `interrupt_after: true`.

**What it does** — Turns a raw dataset into an EDA report, a classified problem
definition, a **frozen** CV fold configuration and a leakage audit, then critiques all four.

**Logic**
- `data_analyst` has the LLM *generate* EDA Python, then runs it through
  `code_executor` — never inline `exec`. Writes both `reports/eda_report.md` and
  `notebooks/01_eda.ipynb`; sets `eda_report_path`.
- `problem_framer` classifies problem type + success metric into
  `reports/problem_definition.json`; sets `problem_definition_path`. The
  `success_metric` string it writes is what `score_evaluator` later reads to decide
  score polarity — a load-bearing cross-phase coupling with no schema enforcing it.
- `validation_strategist` generates fold-splitting code, executes it, parses a single
  JSON line from stdout, and writes `validation/fold_config.json` **exactly once**.
  If the file already exists it raises `FoldsAlreadyFrozenError` **before** any write
  attempt, leaving the existing file byte-identical (CLAUDE.md invariant #1).
- `leakage_auditor` writes `reports/leakage_audit.json`.
- `analysis_critic` returns `pass`/`iterate` and **re-invokes the named target node
  directly** for up to `max_retries` (3) cycles, then forces `pass`
  (CLAUDE.md invariant #5). Targets: the four nodes above.

**Interface**
- Inputs: raw competition data in the workspace; `LabState`.
- Outputs: `reports/eda_report.md`, `notebooks/01_eda.ipynb`,
  `reports/problem_definition.json`, `validation/fold_config.json`,
  `reports/leakage_audit.json`; state fields `eda_report_path`,
  `problem_definition_path`, `validation_config_path`.
- Errors: `FoldsAlreadyFrozenError`; `ValueError` for malformed LLM JSON; executor
  failures surface as `ExecResult` non-zero exits.

**Out of scope** — modeling, feature engineering, fixing leakage it finds (it only reports).

---

## Pipeline Phase 2 — Research

Nodes: `literature_researcher` ∥ `web_researcher` → `competition_analyst` →
`memory_manager`. First two run in parallel (max 2 concurrent, CLAUDE.md invariant #6).

**What it does** — Gathers external knowledge (papers, web, top Kaggle kernels) into
the competition's RAG store, then consolidates it.

**Logic**
- `literature_researcher` queries arXiv + Semantic Scholar; `web_researcher` queries
  Tavily; `competition_analyst` pulls Kaggle's top-voted public kernels via
  `kaggle_client.list_top_kernels`.
- Each node has the **LLM extract structured `IndexDocument` metadata** per source and
  indexes the documents itself — `RagStore` never calls an LLM.
- `literature_researcher` and `web_researcher` also write human-readable reports
  (`reports/literature_research.md`, `reports/web_research.md`).
- `memory_manager` runs **after** the others have indexed, retrieves a window of the
  store, deduplicates near-identical entries and re-scores relevance, so later phases
  query a consolidated store.
- Shared helpers live in `_research_common.py` (which carries its own copy of
  `relative_to_workspace` — Phase 2 was out of the T-020 hoisting scope).

**Interface**
- Inputs: problem definition + EDA report; external APIs (arXiv, Semantic Scholar,
  Tavily, Kaggle); `RagStore`.
- Outputs: indexed documents in the competition's Chroma collection;
  `reports/literature_research.md`, `reports/web_research.md`.
- Errors: external API failures; missing `TAVILY_API_KEY` / Kaggle credentials.

**Out of scope** — deciding the modeling strategy (Phase 4), running code,
cross-competition retrieval.

---

## Pipeline Phase 3 — Baseline

Nodes: `baseline_designer` → `baseline_runner`. **Runs only at
`current_iteration == 0`**, enforced by the supervisor (CLAUDE.md invariant #4).

**What it does** — Establishes the single permanent benchmark every later experiment
is measured against.

**Logic**
- `baseline_designer` (LLM) designs a **non-trivial but non-tuned** experiment into
  `experiments/baseline/design.json`, injecting the fixed `cv_strategy_ref` pointer
  (`validation/fold_config.json`) itself rather than trusting the LLM to name the file.
- `baseline_runner` (compute) executes that design against the **frozen** folds, logs
  the run to MLflow, and writes `experiments/baseline/results.json`.
- `validation/fold_config.json` is **read-only** here.
- `mlflow` is imported only in `baseline_runner`, never in `ComputeNode`, so the base
  class stays dependency-free.
- The resulting `baseline_score` / `baseline_results_path` are set once and **never
  overwritten**.

**Interface**
- Inputs: `reports/problem_definition.json`, `reports/eda_report.md`,
  `validation/fold_config.json`, processed data.
- Outputs: `experiments/baseline/design.json`, `experiments/baseline/results.json`,
  an MLflow run; state `baseline_score`, `baseline_results_path`.
- Errors: `ValueError` for a malformed design payload; MLflow/training errors propagate.

**Out of scope** — hyperparameter tuning, feature engineering, beating the baseline.

---

## Pipeline Phase 4 — Design

Nodes: `solution_architect` → `feature_engineer` → `analysis_critic`. Sequential,
`interrupt_after: true`. This is the **loop head** — Phase 6 routes back here.

**What it does** — Decides the modeling strategy and the feature transformations for
the current iteration.

**Logic**
- `solution_architect` queries the `RagStore` for modeling-strategy findings and reads
  the Phase 3 baseline results, then writes
  `design/iteration_{current_iteration}/solution_plan.json`
  (`model_families`, `order`, `ensembling_strategy`, `rationale`). It carries **no
  `problem_type` field** — that stays in `problem_definition.json`, a distinction
  `specialist_selector` depends on.
- `feature_engineer` writes `design/iteration_{iteration}/feature_spec.json`
  (encodings, null handling, interactions). It degrades to `{}` on a missing or
  unreadable upstream artifact rather than raising — a partially-completed upstream
  phase must not crash the graph.
- `analysis_critic` gates both, same `pass`/`iterate` + forced-pass mechanics as Phase 1.
- Both plans are **per-iteration paths**, so a later iteration never overwrites an
  earlier one's design.

**Interface**
- Inputs: `RagStore`, `experiments/baseline/results.json`, EDA report,
  `problem_definition.json`.
- Outputs: `solution_plan.json`, `feature_spec.json`; state `solution_plan_path`,
  `feature_spec_path`.
- Errors: `ValueError` for malformed LLM JSON.

**Out of scope** — writing training code (Phase 5), scoring (Phase 6).

---

## Pipeline Phase 5 — Implementation

Nodes: `specialist_selector` → `coder` → `code_critic`. Sequential.

**What it does** — Picks exactly one specialist for this iteration, produces a concrete
experiment design, generates the training script, and critiques it.

**Logic**
- `specialist_selector` (compute, no LLM) reads **two** artifacts —
  `solution_plan.json` *and* `problem_definition.json` — because the problem type is
  only in the latter. Both reads degrade to `{}` on missing/unreadable/non-dict content.
- Selection is a **deterministic 4-branch keyword precedence**, first match wins, over
  one normalized text blob (problem type + model families + order + rationale,
  lowercased, separators collapsed), matched with word-boundary regexes:
  1. time-series keywords (`forecast`, `arima`, `prophet`, …) → `timeseries_specialist`
  2. NLP keywords (`nlp`, `text`, `bert`, `tfidf`, `embedding`, …) → `nlp_specialist`
  3. deep-learning keywords (`neural`, `cnn`, `rnn`, `pytorch`, `lstm`, …) →
     `deep_learning_specialist`
  4. no match → `classical_ml_specialist` (default)
  Time-series and NLP are checked **before** deep learning on purpose: an LSTM plan for
  a forecasting or text problem should route to the problem-type specialist, not the
  architecture-driven one (`context/decisions/T-023.md`).
- **`ensemble_specialist` is an independent override**, not a branch of the above. It is
  chosen only when **both** (a) `state["experiments"]` already holds ≥ 2 entries — checked
  first and short-circuiting, so it can never be selected earlier no matter what the plan
  says — **and** (b) `solution_plan.json`'s `ensembling_strategy` is a non-empty string
  that does not itself say "no ensembl…".
- The selector **dispatches to the chosen specialist directly** via `resolve_node`,
  exactly once — no loop, no retry — and returns that specialist's own delta, defensively
  coerced to a `dict`.
- The five specialists each write `experiments/exp_{iteration}/design.json` against the
  shared contract in `_experiment_design.py`: `DESIGN_KEYS` (+ `base_experiments` for
  ensembles), Optuna param types `int`/`float`/`categorical`, param-name and
  preprocessing-step regexes, a `2**53` exact-integer bound, and a
  `FORBIDDEN_CV_KEYS` blocklist that **prevents a specialist from redefining CV** —
  `cv_strategy_ref` always points at the frozen fold config.
  Every validation failure raises `ValueError` and nothing else, so a future
  critic-retry wrapper has exactly one exception type to catch.
- `code_critic` reviews the generated training script against a
  reproducibility / leakage / faithfulness rubric, re-invoking `coder` for up to 3
  cycles, then forcing `pass`.

**Interface**
- Inputs: `solution_plan.json`, `problem_definition.json`, `feature_spec.json`,
  `validation/fold_config.json` (fold summary only).
- Outputs: `experiments/exp_{iteration}/design.json`; (once `coder` lands)
  `src/train.py`, `src/features.py`, `src/models.py` in the workspace.
- Errors: `ValueError` for any design-contract violation.

**Out of scope (not yet built)** — **`coder` is not implemented** (T-029, blocked).
`resolve_node` currently substitutes `NoOpNode`, so Phase 5 produces a design but no
training script, and `code_critic` has nothing real to review.

### Untested behavior
> **WARNING: `coder` does not exist.** Every downstream contract that assumes a
> `results.json` with `cv_score` / `feature_importance` (Phase 6) is therefore
> unexercised end-to-end. `feature_importance_extractor` in particular reads a payload
> no landed node currently writes.

---

## Pipeline Phase 6 — Evaluation

Nodes: `score_evaluator` → `feature_importance_extractor` → `error_analyst` →
`hypothesis_generator` → `experiment_designer`. Sequential, `interrupt_after: true`.

**What it does** — Scores the completed experiment, updates the running best, and
decides what to try next.

**Logic**
- `score_evaluator` is the **sole writer** of `best_score`, `best_experiment_path`,
  `last_score`, `score_delta` and `iterations_without_improvement` anywhere in `src/`.
- **Score-polarity normalization lives here**, because `LabState` has no polarity
  field. `success_metric` from `problem_definition.json` is matched
  case-insensitively **with every non-alphanumeric separator stripped** against a
  curated minimize-set (RMSE, LogLoss, MAE, …) and sign-flipped before it ever
  touches `best_score`. The separator-stripping is deliberate: real output spells the
  same metric as `log_loss`, `Log-Loss`, `Log Loss`, and an exact match would default
  those to "maximize" and get the sign backwards. Unknown or absent metric ⇒ maximize.
- `best_score`/`best_experiment_path` update **only on improvement**
  (CLAUDE.md invariant #3).
- Non-finite and non-numeric scores are coerced to "unavailable" with an explicit
  reason rather than crashing or poisoning the best score.
- `feature_importance_extractor` **extracts, never computes** — there is no `shap`
  import, because a missing transitive import in a landed node module fails the
  *entire* graph build. It reads the pre-computed `feature_importance` payload from
  `results.json`, gates on an explicit tree-ensemble allow-list (importances are
  meaningless for linear/NN families), ranks them, and writes a report. It **always
  returns `{}`** — no `LabState` field exists for this artifact. This makes
  `design.md`'s "Python + SHAP" description stale; `docs/pipeline.md` carries the
  correction.
- Both nodes read experiment artifacts through `_evaluation_common`, whose readers
  **degrade to empty/`None` on any I/O or parse failure and never raise**, so Phase 6
  stays invokable standalone.

**Interface**
- Inputs: `experiments/exp_{iteration}/results.json`, `reports/problem_definition.json`,
  current `LabState` scores.
- Outputs: updated score fields; a ranked feature-importance report in the workspace.
- Errors: none raised by the compute nodes — failures are reported as
  "score unavailable" reasons.

**Out of scope (not yet built)** — `error_analyst`, `hypothesis_generator` and
`experiment_designer` are **not implemented** (T-032, available); `NoOpNode` stands in,
so the iteration loop currently has no LLM-driven "what to try next" step.

---

## Pipeline Phase 7 — Delivery

Nodes: `reviewer` → `report_writer` → `kaggle_client`. Sequential. Reached when
`iterations_without_improvement >= max_iterations`.

**What it does** — Intended to review the final repository, write the final report, and
submit to Kaggle.

**Out of scope (not yet built)** — **none of the three nodes is implemented** (T-033,
available). All three resolve to `NoOpNode` today. The `kaggle_client` *tool*
(`src/tools/kaggle_client.py`) exists and is tested; the *node* that would call it in
Phase 7 does not.

---

## API backend (`src/api/`)

**What it does** — Intended FastAPI backend: run management, SSE event stream, chat
explainer, Kaggle/MLflow proxying.

**Out of scope (not yet built)** — `src/api/` contains **only an empty `__init__.py`**.
T-034 (run management) is available; T-035 (SSE), T-036 (chat explainer) and T-037
(Kaggle/MLflow) are blocked. `design.md` § UI architecture documents the intended
endpoint contract but **no JSON response schemas**, which is why the frontend's types
are provisional.

### Untested behavior
> **WARNING: no backend and no backend tests.** Every frontend↔backend contract below
> is an unverified guess.

---

## Frontend (`frontend/`)

**What it does** — React 19 + Vite + TypeScript UI: run sidebar, pipeline view,
experiments table, file viewer, chat.

**Logic**
- `api/client.ts` is the whole client surface: `listRuns`, `createRun`,
  `subscribeToRunEvents` (SSE), `connectChat` (WebSocket), `resumeRun`, `submitRun`,
  `openMlflow`. `fetch` is **injected** (`FetchLike`), which is what makes it testable
  with no network.
- `API_BASE` comes from `import.meta.env.VITE_API_BASE`, defaulting to `''` (same origin).
- `Layout.tsx` (97 lines) and `Sidebar.tsx` are the only real components; `Chat.tsx`,
  `ExperimentsTable.tsx`, `FileViewer.tsx` and `PipelineView.tsx` are **8-line
  placeholders**.
- npm is the package manager (`package-lock.json` committed) — see
  `context/decisions/T-038.md`.

**Interface**
- Inputs: the FastAPI backend over HTTP/SSE/WebSocket.
- Outputs: rendered UI; run-control calls.
- Errors: fetch/SSE failures surface through the client's handlers.

**Out of scope (not yet built)** — the four placeholder components (T-039, T-040,
T-041, T-042, all available).

### Untested behavior
> **WARNING: only `api/client.ts` and `Layout.tsx` have tests**
> (`client.test.ts`, `Layout.test.tsx`). Additionally, **`api/types.ts` is explicitly
> marked PROVISIONAL in-source**: the response shapes were guessed from `design.md`
> because the backend does not exist. They must be reconciled against the real routes
> when `src/api/` lands.

---

## Cross-module flows

### Flow 1 — Graph construction (every run)
1. `GraphBuilder.build(run_id)` loads the seven `config/phases/*.yaml` via
   `load_phase_config` → `PhaseConfig`.
2. For each phase it imports `src/graph/phases/phase{N}_{name}.py`, which delegates to
   `build_phase_subgraph(config, resolve_node)`.
3. `resolve_node(name)` imports `src/nodes/{llm|compute}/{name}.py` and finds the one
   class whose `name` attribute matches the stem. **Missing module ⇒ `NoOpNode`;
   broken module ⇒ `GraphBuilderError`.**
4. Phase subgraphs are added as nodes of the main graph; the supervisor's two
   conditional edges are wired with `path_map`.
5. `build_checkpointer(run_id)` attaches a SQLite saver at `runs/{run_id}/`.
6. A `JsonlCallbackHandler` writes `runs/{run_id}/execution.jsonl` throughout execution.

### Flow 2 — An LLM node executing
`resolve_node` → `cls()` → `__call__(state)` → `load_agent_config(name)` →
`PromptLoader.load(agent, prompt_version)` → `LLMFactory.get(model_role)` →
`_build_messages` (system prompt + `trim_context(state["messages"], 10)` + node-specific
input) → model invoke → `_write_output` (validate, write through `WorkspaceManager`,
receiving an **absolute** path) → `_build_output_state` (store the path into `LabState`).
A later node reading that path calls `relative_to_workspace` first.

### Flow 3 — Freezing the CV folds (write-once)
`validation_strategist` generates fold code → `code_executor.execute` runs it in a
subprocess → a single JSON line is parsed from stdout → **existence check first** — if
`validation/fold_config.json` exists, `FoldsAlreadyFrozenError` is raised before any
write → otherwise `WorkspaceManager.write_json` writes it atomically. From here on,
`baseline_runner` and all five specialists read it and never write it; `cv_strategy_ref`
in every `design.json` is injected by the node, not the LLM, and `FORBIDDEN_CV_KEYS`
rejects any design that tries to redefine CV.

### Flow 4 — Research → RAG → strategy
`literature_researcher` ∥ `web_researcher` (parallel, 2 max) and `competition_analyst`
each have their LLM emit structured metadata, build `IndexDocument`s, and call
`RagStore.index()` → list-valued metadata is flattened for Chroma; the collection name
is hash-suffixed so competitions cannot collide → `memory_manager` retrieves,
deduplicates and re-scores → `solution_architect` (Phase 4) queries the consolidated
store to write `solution_plan.json`.

### Flow 5 — The iteration loop
Phase 4 (design) → Phase 5 (specialist selection + design.json + *coder, missing*) →
Phase 6 (`score_evaluator` normalizes polarity, updates best **only on improvement**,
increments `iterations_without_improvement` when there is none) → `interrupt_after`
human checkpoint → `supervisor`: `iterations_without_improvement >= max_iterations` ?
Phase 7 : back to Phase 4. The baseline (Phase 3) is never re-entered, because the
supervisor only routes there from Phase 2 at `current_iteration == 0`.

### Flow 6 — Critic retry (both critics)
Target node writes its artifact → critic node reads it and returns `pass`/`iterate` →
on `iterate` the critic **calls the target node directly** (not a graph edge) → up to
`max_retries` (3, from the phase YAML `critic:` block) → on exhaustion the critic
**forces `pass`** so the graph always makes progress (CLAUDE.md invariant #5).

### Flow 7 — Human checkpoint
Phases 1, 4 and 6 declare `interrupt_after: true`; LangGraph pauses and the
checkpointer persists state → the UI renders `checkpoint_summary` → the human's text
lands in `human_feedback` → the run resumes with
`graph.invoke(None, config={"thread_id": run_id})`. Checkpoints are **forward-only**:
no interrupt re-runs a completed phase, and `human_feedback` never changes routing —
`supervisor` does not read it.

---

## Global invariants

These hold across modules and are enforced by tests and review, not by types:

1. `validation/fold_config.json` is write-once — frozen after Pipeline Phase 1.
2. `WorkspaceManager` is the sole file-I/O point to the workspace.
3. `best_experiment_path` / `best_score` update only on improvement.
4. Baseline (Phase 3) runs only at `current_iteration == 0`.
5. Critics enforce `max_critic_retries` then force `pass` — no infinite loops.
6. Max 2 LLM agents run concurrently (Phase 2 only).
7. Prompts live in `config/prompts/`, never inline in Python.
8. Compute nodes never import an LLM module.

## Project-wide out of scope

- The agent system never writes into the ML repository it generates except through
  `WorkspaceManager`; the two repos never share code.
- No node runs untrusted third-party code outside `code_executor`'s subprocess, and
  `code_executor` provides **no real sandbox** — no container, no seccomp, no resource caps.
- No cross-competition RAG retrieval (structurally prevented by per-competition collections).
- No Docker image and no CI workflow exist yet (T-043 blocked, T-044 available):
  `docker/` and `.github/workflows/` are absent from the repo.
