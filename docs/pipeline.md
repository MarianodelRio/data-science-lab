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

`config/phases/*.yaml` is the **only** place phases are composed — the 7 files list each phase's
`nodes`, `sequence`, `parallel_nodes`, `critic`, and `interrupt_after`. Node tasks never edit
these files; they only add a node name to an existing phase's `nodes`/`sequence` list (or, per
CLAUDE.md's modularity section, register a brand-new agent this way). `PhaseConfig`
(`src/config/schema.py`) is the frozen shape every YAML must validate against, loaded via
`load_phase_config` (`src/config/loaders.py`).

### Node-module convention

A node lives at `src/nodes/{llm|compute}/{name}.py` and exposes exactly one class, **defined in
that module** (not merely imported into it), with:
- a `name` class attribute equal to the module's filename stem
- a no-argument constructor
- `__call__(self, state: LabState) -> dict` — a partial `LabState` update, following LangGraph's
  node-return convention

**`name` must be a plain class attribute** (`name = "..."` at class body level), **not a Pydantic
model field.** A node class written as a Pydantic v2 `BaseModel` subclass with `name: str = "..."`
declared as a typed field does NOT satisfy `_find_node_class`'s lookup: Pydantic v2 doesn't expose
field defaults via plain `getattr` on the class itself (only on instances, after
validation/`__init__`), so `getattr(cls, "name", None)` sees nothing and the class won't match.
This already fails loudly — `_find_node_class` raises `GraphBuilderError` for zero matches, it does
not silently no-op — but the error message alone doesn't explain *why* a seemingly-correct
Pydantic-style node class doesn't resolve, hence this explicit call-out.

`src/graph/node_resolver.py`'s `resolve_node(name)` is how every node name in a phase YAML
becomes an actual callable: it tries `src.nodes.llm.{name}` then `src.nodes.compute.{name}`, finds
the matching class via `_find_node_class`, and instantiates it. If neither module exists yet
(`ModuleNotFoundError` raised while importing the `src.nodes.{kind}.{name}` module path itself), it
falls back to `NoOpNode(name)` (`src/graph/nodes_noop.py`) — a placeholder that never raises and
never mutates `LabState` beyond an empty return. This is what lets `GraphBuilder.build()` compile a
full 7-phase graph today, before any node task has landed a single real node. Two other cases raise
`GraphBuilderError` (`src/graph/errors.py`) instead of silently no-opping, because both indicate a
real bug in a landed module rather than "not implemented yet":
- the module exists but one of *its own* transitive imports is missing (a `ModuleNotFoundError`
  whose `.name` does not match the `src.nodes.{kind}.{name}` path being resolved — i.e. some other,
  nested import inside the node module failed)
- the module exists and imports cleanly but exposes zero or more than one matching class

### Phase subgraph assembly

Each phase is its own compiled LangGraph subgraph, built generically by
`src/graph/phases/generic.py`'s `build_phase_subgraph(config, resolve_node)` from nothing but its
`PhaseConfig`:
- every name in `nodes` becomes a graph node via `resolve_node`
- with no `parallel_nodes`, `sequence` is chained pairwise
- with `parallel_nodes` set (only Phase 2 today — `literature_researcher ‖ web_researcher`), the
  contiguous run of parallel nodes inside `sequence` fans out from the node before it (or `START`)
  and fans back in to the node after it (or `END`); the rest of `sequence` chains normally
- entry/finish points are `sequence[0]`/`sequence[-1]`

Each of the 7 thin `src/graph/phases/phase{N}_{name}.py` modules just calls this with its own
`load_phase_config(...)` result — no per-phase wiring logic duplicated.

### Main graph — `GraphBuilder`

`src/graph/builder.py`'s `GraphBuilder.build(run_id, runs_dir=None)` assembles the 7 compiled
phase subgraphs into the single top-level graph:

```
START
  → phase1_understanding  (interrupt)
  → phase2_research
      ├─ current_iteration == 0 → phase3_baseline → phase4_design
      └─ otherwise              → phase4_design
  → phase4_design          (interrupt)
  → phase5_implementation
  → phase6_evaluation      (interrupt)
      ├─ iterations_without_improvement >= max_iterations → phase7_delivery
      └─ otherwise                                        → phase4_design (loop)
  → phase7_delivery
  → END
```

Each phase subgraph is wrapped so its top-level node also stamps `phase={stem}` into the returned
state delta — this is how the supervisor (see below), running at the top level, learns which
phase just finished. `LabState.phase`'s write-ownership was previously undefined; `GraphBuilder`
establishes it here.

`interrupt_after` is computed dynamically from the 7 loaded `PhaseConfig`s (never hardcoded):
today it resolves to `["phase1_understanding", "phase4_design", "phase6_evaluation"]`, matching
design.md's three mandatory human checkpoints.

### Supervisor

`src/graph/supervisor.py`'s `supervisor(state) -> str` is the pipeline's only conditional-edge
routing logic — pure Python, deterministic, no LLM. It is called from two places:
- after `phase2_research`: routes to `phase3_baseline` only when `current_iteration == 0`
  (CLAUDE.md invariant #4 — baseline runs exactly once), otherwise straight to `phase4_design`
- after `phase6_evaluation`: routes to `phase7_delivery` once
  `iterations_without_improvement >= max_iterations`, otherwise loops back to `phase4_design`

Called from any other phase, it raises `GraphBuilderError` — that's a real assembly bug, not a
normal code path.

**Critic-retry and specialist-dispatch are deliberately not supervisor concerns.** `LabState` has
no verdict/retry-count/selected-specialist field (adding one is a protected-contract change, not
yet approved), so `analysis_critic`, `code_critic`, and `specialist_selector` own their control
flow internally instead: a critic's own node function re-invokes its target node(s) directly (via
the same `resolve_node` mechanism) up to `max_retries`, entirely inside its own function, and
`specialist_selector` internally invokes exactly one chosen specialist the same way. Neither
surfaces as a graph-level conditional edge. This means `PhaseConfig.sequence` for phase1/phase4/
phase5 stays a flat one-pass list — the YAML lists every specialist/critic node in sequence, but
the *actual* runtime dispatch/retry behavior lives inside the node implementations landed by
T-016/T-023/T-030, not in `GraphBuilder`. See `context/decisions.md` (T-009) for the full
rationale.

### Checkpointer

`src/graph/checkpointer.py`'s `build_checkpointer(run_id, runs_dir=None)` builds a `SqliteSaver`
backed by `{runs_dir}/{run_id}/checkpoint.db` (`runs_dir` defaults to the repo-root `runs/`
directory; the parameter exists purely for test injection, mirroring `src/config/loaders.py`'s
`base_dir` convention). It's constructed via `SqliteSaver(sqlite3.connect(path,
check_same_thread=False))` rather than `SqliteSaver.from_conn_string(...)` — in the installed
`langgraph-checkpoint-sqlite` version, `from_conn_string` is a context-manager generator, which is
awkward for a checkpointer that needs to stay alive for the lifetime of the compiled graph well
past `GraphBuilder.build()`'s return.

Resuming after a crash/restart: build a new `GraphBuilder` pointed at the same `runs_dir`/`run_id`
and `graph.invoke(None, config={"configurable": {"thread_id": run_id}})` — LangGraph's checkpoint
mechanism skips already-completed nodes and continues from the next one.

**Actual resume granularity is finer than the 7 top-level phase nodes.** It's tempting to assume
checkpointing only happens at phase-node boundaries (there are only 7 nodes in the top-level
graph GraphBuilder itself wires), but LangChain-core's ambient `RunnableConfig` context-var
propagation causes the *same* top-level `SqliteSaver` to also checkpoint individual sub-nodes
**inside** each phase subgraph — resume after a crash mid-phase does not re-execute sub-nodes of
that phase already completed before the crash. This has been verified against this task's actual
code: no re-execution of already-completed sub-nodes on resume, no cross-iteration state leakage
on the phase6→phase4 loop-back. It is correct, safe behavior, but it is an emergent property of
LangChain/LangGraph's internals (context-var propagation into nested subgraph invocations), **not**
an explicit design choice made by `generic.py`/`builder.py`'s own code — neither module configures
or relies on sub-node checkpointing directly. A future LangChain/LangGraph version bump that
changes this internal propagation behavior is a risk worth being aware of; it would not show up as
a diff in this repo.

One more operational note: `checkpoint.db` accumulates one row per sub-node execution over a run's
lifetime, with no pruning/vacuum logic anywhere in this task's code. Not a bug — just worth
flagging for whoever eventually needs to manage `runs/` directory growth over long-running or
many-iteration runs.

### Interrupt placement

Interrupts fire *after* a whole phase subgraph completes (not after individual nodes inside it) —
`interrupt_after` names top-level phase-stem nodes (`phase1_understanding`, `phase4_design`,
`phase6_evaluation`), never nodes inside a phase's `sequence`. This is a separate concern from the
checkpointing-granularity note above: `interrupt_after` controls where the graph *pauses for a
human*, which stays fixed at the 7 phase-node boundaries by this task's own explicit config; it
does not change how finely completed work gets persisted for resume.

## The 7 phases

> Skeleton — one subsection per phase (`Understanding`, `Research`, `Baseline`, `Design`,
> `Implementation`, `Evaluation`, `Delivery`), each documenting its nodes, sequence, critic, and
> interrupt behavior. Populated incrementally by each phase's implementing task.

## Node classification

> Skeleton — table of LLM nodes vs pure Python nodes vs tools, populated as each node lands.

### ComputeNode base class

`src/nodes/compute/base.py` — `ComputeNode` is the base class every pure-Python (non-LLM) node
under `src/nodes/compute/{name}.py` subclasses. It follows the same node-module convention as LLM
nodes (see "Node-module convention" above): concrete subclasses declare `name` as a plain class
attribute equal to the module's filename stem and are constructible with no arguments, so
`resolve_node`'s `cls()` call works unchanged for compute nodes.

- **Lifecycle:** `__call__(self, state: LabState) -> dict` delegates to an abstract `run(self,
  state: LabState) -> dict` hook that subclasses implement — `run` computes and returns a partial
  `LabState` update (the delta), `__call__` is just the LangGraph-facing entrypoint.
- **`ComputeNode` itself is abstract** (`run` is `@abstractmethod`): instantiating it directly
  raises `TypeError`, so a landed compute node that forgets to implement `run` fails loudly at
  `resolve_node`'s `cls()` call, not silently.
- **Workspace access:** `self.workspace(state) -> WorkspaceManager` builds a `WorkspaceManager`
  rooted at `state["workspace_path"]`, constructed fresh on every call (there is no `__init__` to
  cache it in, since nodes are instantiated via `cls()`). This is the only sanctioned way a
  `ComputeNode` subclass touches the workspace filesystem, per `WorkspaceManager` being the sole
  file-I/O point.
- **No LLM anywhere:** unlike `LLMNode` (`src/nodes/llm/base.py`, T-010) — which carries a model,
  an `AgentConfig`, prompt loading, and context trimming — `ComputeNode` has none of that. It
  imports nothing from `src/llm` or any `langchain` package; its only dependency beyond `src.state`
  is `WorkspaceManager`.
- **Reference example:** `tests/unit/nodes/compute/test_base.py` implements a small
  `_DoubleIterationNode` fixture that reads `state["current_iteration"]`, writes a marker file via
  `self.workspace(state)`, and returns `{"last_score": ...}` — the canonical run→delta shape a real
  compute node follows.

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

### rag

`src/tools/rag.py` (`RagStore`) + `src/memory/store.py` (Chroma wrapper + `IndexDocument`
schema) — one Chroma collection per competition (`rag_{sanitized(competition_name)}`),
local embeddings via `sentence-transformers` (`all-MiniLM-L6-v2`), no external API/LLM calls
in this module.

- `RagStore(competition_name, chroma_host=None, chroma_port=None)` — both host/port given
  -> `chromadb.HttpClient` (the Docker `chroma` service, `config/settings.yaml`
  `workspace.chroma_host`/`chroma_port`); omitted -> `chromadb.EphemeralClient()` (in-memory,
  used by tests, no Docker dependency).
- `.index(documents: list[IndexDocument]) -> None` / `.query(text, where=None, n_results=10)
  -> list[IndexDocument]`.
- `IndexDocument` (`src/memory/store.py`) is the structured-extraction contract populated by
  research nodes (T-017 `literature_researcher` etc.) before calling `.index()` — this module
  never extracts metadata itself, it only stores/embeds/retrieves what it's given.
- `where={"problem_type": {"$in": [...]}}` (and same for `methods_used`,
  `dataset_characteristics`) is translated internally into a Chroma `$or`-of-`$contains`
  clause, because these fields are stored as Chroma list-valued metadata and Chroma's `$in`
  does not match list-valued metadata directly. Scalar-field `where` clauses (`source`,
  `relevance_score`) pass through unchanged.

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
