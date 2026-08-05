# Pipeline Reference

Primary architecture doc for the LangGraph pipeline. Updated by the coder on every task that
adds/changes a node, a phase, or a pipeline-level contract. See `design.md` for full rationale;
this file tracks the current implemented state.

## State

`LabState` (`src/state.py`) is the single shared coordinator state threaded through every
LangGraph node in the pipeline. It holds only file paths, scalars, and control fields — no
large data structures in memory. Content produced by nodes (EDA reports, experiment results,
generated code) lives on disk in the workspace; the state only holds pointers to it.

Field groups:

- **Input** — `competition_name`, `workspace_path`: identify the run and its workspace root.
- **File pointers** — `eda_report_path`, `problem_definition_path`, `validation_config_path`,
  `baseline_results_path`, `solution_plan_path`, `feature_spec_path`: paths into the workspace
  for artifacts produced by earlier phases.
- **Control** — `phase`, `current_iteration`, `max_iterations`,
  `iterations_without_improvement`: drive the supervisor's routing and the iteration loop's
  stop conditions.
- **Scores** — `baseline_score`, `best_score`, `last_score`, `score_delta`: the permanent
  benchmark, the running best, the most recent experiment's score, and the delta between them.
- **Experiment index** — `experiments` (metadata only: id/path/cv_score/iteration/model) and
  `best_experiment_path`: a lightweight index over experiments whose full results live in
  workspace files.
- **Human checkpoint** — `checkpoint_summary` (markdown rendered in the UI),
  `human_feedback`: the interrupt/resume contract with the human reviewer.
- **LLM context** — `messages`: trimmed per node (last N messages + node-specific input),
  merged via LangGraph's `add_messages` reducer.

`new_state(competition_name, workspace_path)` is a pure factory with no I/O that builds a
fresh `LabState` for a new run: `current_iteration=0`, `iterations_without_improvement=0`,
`max_iterations=10` (override via keyword arg; matches `config/settings.yaml`'s
`execution.max_iterations`), `phase=""`, `best_score=float("-inf")` (so the first experiment
always counts as an improvement), all other scores `0.0`, all path-pointer/checkpoint fields
`""`, and fresh `experiments`/`messages` list literals (never shared across calls).

**State-mutation rules:**

- `validation_config_path` is immutable after Pipeline Phase 1.
- `baseline_results_path` and `baseline_score` are set once (Pipeline Phase 3) and never
  overwritten.
- `best_experiment_path` and `best_score` update only when a new experiment's score improves
  on the current best. **Scores must be normalized so that "higher is better" before being
  written to `last_score`/`best_score`** — the state contract itself has no polarity field, so
  whichever node computes `last_score` (Pipeline Phase 6, `score_evaluator`) is responsible for
  sign-flipping minimize-oriented metrics (RMSE, LogLoss, MAE, etc.) before writing.
- `messages` is trimmed per node via the `add_messages` reducer plus
  `context.trim_strategy`/`max_messages_per_node` from `config/settings.yaml`.

**Field write-ownership not yet defined:** `phase` (which node sets it and when — expected to be
set by the supervisor/graph entry on each phase transition) and the exact formula for
`score_delta` (vs. best? vs. baseline? vs. previous iteration?) are not specified by `LabState`
itself. These are contracts for the implementing nodes to establish, not this module — see
`context/discoveries.md` for the open item tracking this.

**Concurrent-write note:** only `messages` has a LangGraph reducer (`add_messages`). All other
fields use the default `LastValue` channel, which raises `InvalidUpdateError` if two nodes write
the same key within one super-step. Pipeline Phase 2 runs `literature_researcher` and
`web_researcher` concurrently (design.md's one sanctioned parallel step) — any future node pair
that needs to write the same `LabState` key in the same step will need that field upgraded with
an explicit reducer. See `context/discoveries.md`.

## Graph topology

> Skeleton — populated when `src/graph/` lands. Document the main graph diagram (phase sequence,
> interrupt points, the phase4↔phase6 iteration loop), the supervisor conditional edge, and the
> SQLite checkpointer setup.

## The 7 phases

> Skeleton — one subsection per phase (`Understanding`, `Research`, `Baseline`, `Design`,
> `Implementation`, `Evaluation`, `Delivery`), each documenting its nodes, sequence, critic, and
> interrupt behavior. Populated incrementally by each phase's implementing task.

## Node classification

> Skeleton — table of LLM nodes vs pure Python nodes vs tools, populated as each node lands.

## Tools

> Skeleton — one subsection per tool (`code_executor`, `kaggle_client`, `rag`,
> `workspace_manager`), populated by the task that implements each tool.

### code_executor

`src/tools/code_executor.py` — runs arbitrary Python source in a subprocess and returns an
`ExecResult` (`returncode`, `stdout`, `stderr`, `timed_out`); never raises on nonzero exit.

- Code is run via `sys.executable -c <code>` (no temp files).
- Runs in its own process group (`start_new_session=True`); on timeout the whole group is
  killed via `os.killpg` + `SIGKILL`, so no orphaned children survive.
- `timeout` defaults to `settings.execution.code_executor_timeout_seconds` when the caller
  passes `None`/omits it; callers that pass an explicit value never trigger a `Settings.load()`
  call.

### kaggle_client

`src/tools/kaggle_client.py` — thin wrapper around the `kaggle` package for downloading
competition data, submitting predictions, and reading back the latest score.

- `download(competition, dest_dir, api=None) -> list[str]` — downloads the competition's data
  via `competition_download_files(competition, path=dest_dir, force=True, quiet=True)`, extracts
  the resulting `{dest_dir}/{competition}.zip` (this installed `kaggle` version has no built-in
  unzip), removes the archive, and returns the extracted files as `dest_dir`-joined paths.
  Creates `dest_dir` if it doesn't exist.
- `submit(competition, file_path, message, api=None) -> None` — calls
  `competition_submit(file_name=file_path, message=message, competition=competition,
  quiet=True)`.
- `get_score(competition, api=None) -> dict` — calls `competition_submissions(competition)` and
  returns `{"public_score": float, "submitted_at": str}` for the **latest** submission, selected
  via `max(submissions, key=lambda s: s.date)` — list order is not documented as sorted, so index
  0 is not assumed to be latest. Raises `RuntimeError` naming the competition if there are no
  submissions.
- **Injectable API:** every function takes an optional `api: KaggleApiProtocol | None`; when
  omitted, `_default_api()` builds a real `kaggle.api.kaggle_api_extended.KaggleApi` and
  authenticates it. Tests inject a mock and never hit the network.
- **Credentials:** `_default_api()` reads `KAGGLE_USERNAME`/`KAGGLE_KEY` directly from
  `os.environ` (not `Settings`/`ApiKeysConfig`) and raises `RuntimeError(f"Missing required
  environment variable '{name}'")` if either is unset — checked at call time, not at
  module-import time.
- **Why `kaggle` is imported lazily, inside `_default_api()`, after the env check:** the
  `kaggle` package authenticates eagerly on import — `kaggle/__init__.py` constructs a
  `KaggleApi` and calls `authenticate()` at module scope, and because Python always initializes
  a parent package first, even `from kaggle.api.kaggle_api_extended import KaggleApi` triggers
  it. `authenticate()` reads the same env vars, falls back to `~/.kaggle/kaggle.json`, and raises
  `OSError` if neither is present. A module-scope `import kaggle` would therefore crash on import
  in any credential-less environment (e.g. CI) with `kaggle`'s own `OSError`, preempting this
  module's `RuntimeError`. Deferring the import until after `_require_env` has already passed
  avoids that.

### workspace_manager

`src/workspace/workspace_manager.py` — `WorkspaceManager` is the sole file-I/O point to the
generated ML workspace (`~/competitions/{competition_name}/`). All nodes and tools write through
it; no other module touches the workspace filesystem directly.

**API** (see `design.md` § WorkspaceManager API for the full contract):
- `read_json` / `write_json` — JSON round-trip; `write_json` creates parent dirs and returns the
  absolute path written
- `read_text` / `write_text` — plain text, UTF-8
- `write_notebook(relative_path, cells)` — builds a valid `.ipynb` via `nbformat`; `cells` is a
  list of `{"cell_type": "code" | "markdown", "source": str}` dicts
- `experiment_dir(exp_id) -> Path` — computes `experiments/{exp_id}` without creating it
- `ensure_dir(relative_path) -> Path` — creates (idempotently) and returns the directory

**Path safety:** every method accepting `relative_path` rejects absolute paths and any `..`
traversal component, raising `ValueError` — the workspace root cannot be escaped from a relative
path argument.

## RAG

> Skeleton — vector store, embeddings, indexing pipeline, and retrieval pattern, populated by the
> RAG tool task.

## Observability

> Skeleton — local JSONL logs, MLflow experiment tracking, and opt-in LangSmith tracing, populated
> by the observability task.

## Invariants

> Skeleton — the pipeline-level invariants enforced by this codebase (fold immutability, single
> file-I/O point, best-score-only updates, retry caps, etc.), populated as each is implemented and
> enforced in code. See `design.md` § Critical invariants for the current target list.
