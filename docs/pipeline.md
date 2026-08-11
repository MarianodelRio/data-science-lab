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

- `validation_config_path` is immutable after Pipeline Phase 1: `ValidationStrategistNode`
  (`src/nodes/llm/validation_strategist.py`) enforces this by checking for an existing
  `validation/fold_config.json` before writing and raising `FoldsAlreadyFrozenError`
  (`src/nodes/llm/errors.py`) rather than overwriting it.
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
surfaces as a graph-level conditional edge. `PhaseConfig.sequence` for phase1/phase4 stays a flat
one-pass list that lists every critic-retry target node once. Phase 5's YAML is different in one
respect, as of T-023: it does **not** enumerate the 5 specialist names at all — only
`specialist_selector`, `coder`, `code_critic` — since `specialist_selector` dispatches to exactly
one specialist internally via `resolve_node`; listing all 5 in `sequence` too would make
`src/graph/phases/generic.py` chain them in as real, always-executed graph edges, invoking every
specialist in addition to `specialist_selector`'s own internal single-specialist dispatch. See
"Implementation (Phase 5)" below for the selection/dispatch mechanism in detail, and
`context/decisions.md` (T-009, T-023) for the full rationale.

### Human checkpoints

All 3 interrupts (`phase1_understanding`, `phase4_design`, `phase6_evaluation`) are
forward-only — there is no mechanism to re-execute a phase that already completed.
`human_feedback` is a `LastValue` field that persists forward for future nodes to read; no
node reads it yet. The checkpoint after `phase6_evaluation` does not influence `supervisor`'s
routing — that stays 100% automatic on `iterations_without_improvement` vs `max_iterations`,
identical to the other two checkpoints in that the human never changes graph topology. See
`context/decisions.md` (2026-08-06).

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

### Understanding (Phase 1)

`config/phases/phase1_understanding.yaml`'s `sequence`: `data_analyst` → `problem_framer` →
`validation_strategist` → `leakage_auditor` → `analysis_critic`, interrupt after the phase
completes. `data_analyst` (T-013), `problem_framer` (T-014), `validation_strategist` (T-015), and
`leakage_auditor` (T-014) have landed; only `analysis_critic` still resolves to `NoOpNode`.

- **`data_analyst`** (`src/nodes/llm/data_analyst.py`, `LLMNode` subclass, `model_role:
  reasoning`) — the phase's first node. Its `_write_output` override parses a single fenced
  ```python code block out of the LLM's response, runs it through `code_executor.execute`
  (never inline `exec`/`eval`), and writes two workspace artifacts: `reports/eda_report.md`
  (narrative + the subprocess's captured stdout, plus an `## Execution errors` section if the
  run failed or timed out) and `notebooks/01_eda.ipynb` (one markdown cell for the narrative,
  one code cell for the executed script). `_build_output_state` sets
  `state["eda_report_path"]` to the report's path — the notebook path is not tracked in
  `LabState` (no field for it; the notebook is a workspace artifact only, not read by any
  downstream node).
- **`problem_framer`** (`src/nodes/llm/problem_framer.py`, `LLMNode` subclass, `model_role:
  fast`) — runs second. `_build_messages` injects `reports/eda_report.md` (read via its own
  `WorkspaceManager`, from `state["eda_report_path"]`) as an extra `HumanMessage`. `_write_output`
  extracts a JSON object from the response (raw JSON, a single ```json fence, or a single
  unlabeled ``` fence — multiple fenced blocks or invalid JSON raise a `ValueError` naming
  `problem_framer`), validates `problem_type`/`success_metric` as required non-empty strings and
  `constraints` as an optional `list[str]` defaulting to `[]`, then writes
  `reports/problem_definition.json` via `workspace.write_json`. `_build_output_state` sets
  `state["problem_definition_path"]`.
- **`validation_strategist`** (`src/nodes/llm/validation_strategist.py`, `LLMNode` subclass,
  `model_role: fast`) — runs third, selects a CV strategy
  (stratified/group/time_series/adversarial) and freezes concrete fold indices. Its
  `_build_messages` override injects the problem definition + EDA report
  (`state["problem_definition_path"]`/`state["eda_report_path"]`) as an extra `HumanMessage`.
  Its `_write_output` override enforces the write-once guard first — if
  `validation/fold_config.json` already exists it raises `FoldsAlreadyFrozenError`
  (`src/nodes/llm/errors.py`) before doing anything else, including before invoking
  `code_executor.execute` again — then parses a single fenced ```python code block out of the
  LLM's response, runs it through `code_executor.execute` (never inline `exec`/`eval`), parses
  the script's single-line JSON stdout, validates both the presence and the shape/content of
  `strategy`/`n_folds`/`fold_indices`/`seed` (rejecting a structurally-valid-but-garbage payload
  with a `ValueError`, since the file is permanently frozen once written), and writes
  `validation/fold_config.json` containing exactly those four keys. `_build_output_state` sets
  `state["validation_config_path"]` to the written path.
- **`leakage_auditor`** (`src/nodes/llm/leakage_auditor.py`, `LLMNode` subclass, `model_role:
  reasoning`) — runs fourth, after `validation_strategist`. `_build_messages` injects both
  `reports/eda_report.md` and `reports/problem_definition.json` (read via its own
  `WorkspaceManager`, from `state["eda_report_path"]`/`state["problem_definition_path"]`) as an
  extra `HumanMessage`. `_write_output` uses the same JSON-extraction convention as
  `problem_framer` (duplicated locally, per each `LLMNode` subclass staying self-contained),
  validates `leaks` as a required `list`, `severity` as a required non-empty string, and
  `blocks_progression` as a **strict** JSON boolean (a `"true"`/`"false"` string is rejected),
  then writes `reports/leakage_audit.json` via `workspace.write_json`. It does **not** override
  `_build_output_state` — there is no `LabState` field for the leakage audit, so its delta is the
  base class's default `{"messages": [...]}` only.
- **Path-field gotcha both `problem_framer` and `leakage_auditor` work around:**
  `WorkspaceManager.write_text`/`write_json` return an *absolute* path (design.md's
  WorkspaceManager API table), and `_build_output_state` implementations store that value
  verbatim into `LabState` (`eda_report_path`, `problem_definition_path`) — but
  `read_text`/`read_json` require a *relative* path and reject absolute ones. Since these two
  nodes are the first to actually read a path field written by an earlier node, each module
  defines a local `_relative_to_workspace` helper that re-relativizes an absolute stored path
  against the current `WorkspaceManager.workspace_path` before reading (already-relative paths,
  e.g. in unit tests, pass through unchanged).

### Baseline (Phase 3)

`config/phases/phase3_baseline.yaml`'s `sequence`: `baseline_designer` → `baseline_runner`, no
critic, no interrupt. The supervisor (see "Supervisor" above) only routes into this phase when
`current_iteration == 0` (CLAUDE.md invariant #4) — the resulting `baseline_score`/
`baseline_results_path` are the pipeline's single permanent benchmark: written once here, never
re-run, never overwritten for the lifetime of the run (design.md § Phase 3).

- **`baseline_designer`** (`src/nodes/llm/baseline_designer.py`, `LLMNode` subclass, `model_role:
  implementation`) — runs first. `_build_messages` injects both `reports/problem_definition.json`
  and `reports/eda_report.md` (read via its own `WorkspaceManager`, from
  `state["problem_definition_path"]`/`state["eda_report_path"]`, re-relativized via the shared
  `relative_to_workspace` helper — see below) as an extra `HumanMessage`. `_write_output` extracts
  a JSON object from the response (same fence-stripping convention as `problem_framer`/
  `leakage_auditor`), validates `model` (non-empty str), `hyperparameters` (dict), `features`
  (`"all"` or `list[str]`), and `target_column` (non-empty str) — any missing/invalid field raises
  a `ValueError` naming `baseline_designer` — then always injects `cv_strategy_ref:
  "validation/fold_config.json"` itself (never trusted from the LLM, even if the LLM's response
  includes its own value for that key) before writing `experiments/baseline/design.json` via
  `workspace.write_json`. It does **not** override `_build_output_state` — `baseline_runner` reads
  `design.json` back from its fixed, well-known workspace path in the same phase, the same
  convention `fold_config.json` uses, so there is no new `LabState` field for this output.
- **`baseline_runner`** (`src/nodes/compute/baseline_runner.py`, `ComputeNode` subclass — the
  first `ComputeNode` implementation to land beyond the abstract base, T-010) — runs second.
  `run` reads `experiments/baseline/design.json` and `validation/fold_config.json` via
  `self.workspace(state)` (the latter is **read-only**: this node must never write to
  `validation/fold_config.json`, which stays frozen per CLAUDE.md invariant #1 — it only proves
  the frozen folds exist/are readable before generating the training script), generates a fixed
  Python training script (sklearn/LightGBM/XGBoost dispatch keyed off `design["model"]`, using
  `folds["fold_indices"]`'s `{"train": [...], "val": [...]}` shape for cross-validation — the
  script itself re-reads both files from disk at its subprocess `cwd`, avoiding any
  string-interpolation/injection concern from embedding LLM-authored values into source code), and
  executes it via `src.tools.code_executor.execute` (never inline `exec`/`eval`). A nonzero
  `returncode` or `timed_out=True` raises `ValueError` naming the failure (mirroring
  `validation_strategist`'s subprocess-failure handling); the script's stdout contract is exactly
  one JSON line as its last line — `{"cv_score": <float>, "fold_scores": [...], "model": ...}` —
  parsed from the last non-blank stdout line, with malformed/missing-`cv_score` JSON raising a
  `ValueError`. On success it logs to MLflow (`mlflow.set_tracking_uri(Settings.load().workspace
  .mlflow_tracking_uri)`, then inside `with mlflow.start_run(run_name="baseline")`:
  `mlflow.log_params(design["hyperparameters"])` + `mlflow.log_metric("cv_score", cv_score)` —
  `mlflow` is imported only in this module, never in `src/nodes/compute/base.py`), writes
  `experiments/baseline/results.json` (`cv_score`, `fold_scores`, `design`) via
  `workspace.write_json`, and returns `{"baseline_score": float(cv_score),
  "baseline_results_path": results_path}` — both existing `LabState` fields, no new ones added.
- **`relative_to_workspace` (hoisted, T-020):** `src/nodes/llm/base.py` now exports a standalone
  `relative_to_workspace(path, workspace)` function alongside `LLMNode`/`trim_context` — the exact
  logic previously duplicated as a private `_relative_to_workspace` in `problem_framer.py`,
  `leakage_auditor.py`, and `analysis_critic.py` (each deleted its local copy in favor of this
  shared one). `baseline_designer.py` uses it too; `src/nodes/llm/_research_common.py` keeps its
  own separate copy (Phase 2 research nodes, an intentionally distinct module boundary — see its
  own docstring).

### Implementation (Phase 5)

`config/phases/phase5_implementation.yaml`'s `sequence`: `specialist_selector` → `coder` →
`code_critic`, critic targets `[coder]` (`max_retries: 3`), no interrupt. As of T-023, the YAML
deliberately does **not** list any of the 5 specialist names (`classical_ml_specialist`,
`deep_learning_specialist`, `nlp_specialist`, `timeseries_specialist`, `ensemble_specialist`) —
`specialist_selector` owns dispatching to exactly one of them internally (see "Supervisor" above).

- **`specialist_selector`** (`src/nodes/compute/specialist_selector.py`, `ComputeNode` subclass,
  T-023) — the phase's first node, pure Python, no LLM. Reads two upstream artifacts via
  `self.workspace(state)`, each independently degrading to `{}` (never raising) on an unset
  `state[...]` path, a missing/unreadable file, or non-dict JSON content:
  - `state["problem_definition_path"]` → `problem_definition.json` (has `problem_type: str`)
  - `state["solution_plan_path"]` → `solution_plan.json` (has `model_families: list[str]`,
    `order: list[str]`, `ensembling_strategy: str`, `rationale: str`)

  It builds one normalized (lowercased, `-`/`_` collapsed to spaces, whitespace collapsed) text
  blob from `problem_type` + `model_families` + `order` + `rationale`, and selects a specialist
  by this exact fixed precedence (first match wins), matched with word-boundary regexes:
  1. timeseries keywords (`"time series forecasting"`, `"forecast"`, `"arima"`, `"prophet"`) →
     `timeseries_specialist`
  2. NLP keywords (`"nlp"`, `"text"`, `"bert"`, `"transformer"`, `"tfidf"`, `"tf idf"`,
     `"embedding"`) → `nlp_specialist`
  3. deep-learning keywords (`"neural"`, `"cnn"`, `"rnn"`, `"deep learning"`, `"pytorch"`,
     `"lstm"`) → `deep_learning_specialist`
  4. no match → `classical_ml_specialist` (default)

  Timeseries is checked before deep learning, and NLP before deep learning, by design: an
  LSTM/transformer plan for a forecasting or text problem should route to
  `timeseries_specialist`/`nlp_specialist` — the problem-type-driven specialist — not
  `deep_learning_specialist`, an architecture-driven signal that can legitimately co-occur with
  either. Routing by problem type, not architecture, is the more actionable specialist boundary
  (`context/decisions.md`, 2026-08-11 T-023).

  Independent of the 4-branch precedence, `ensemble_specialist` is selected instead whenever BOTH:
  (a) `state["experiments"]` already has >= 2 entries, checked first and short-circuiting
  immediately to the 4-branch precedence when false — `ensemble_specialist` is never selected
  with fewer than 2 experiments, regardless of anything else — AND (b) `solution_plan.json`'s
  `ensembling_strategy` field is a non-empty string that does not itself say "no ensembling"
  (checked via the same normalized-blob word-boundary approach against `"no ensembl"`, a common
  prefix of both "no ensembling" and "no ensemble").

  Once a specialist name is chosen, `run` dispatches to it exactly once via
  `resolve_node(chosen_name)` (`src/graph/node_resolver.py`) and returns that specialist's own
  delta, defensively coerced to `{}` if it isn't a `dict` — the same single-call/merge shape as
  `analysis_critic`'s own `resolve_node(target_node)(...)` retry-target call, minus the
  retry loop. All 5 specialist names currently fall back to `NoOpNode` (T-024–T-028 haven't
  landed yet) — this is `resolve_node`'s documented "not implemented yet" behavior, not a bug.

## Node classification

> Skeleton — table of LLM nodes vs pure Python nodes vs tools, populated as each node lands.

| Node | Type | Phase | Status |
|---|---|---|---|
| `data_analyst` | LLM (`LLMNode`) | 1 — Understanding | Landed (T-013) |
| `problem_framer` | LLM (`LLMNode`) | 1 — Understanding | Landed (T-014) |
| `validation_strategist` | LLM (`LLMNode`) | 1 — Understanding | Landed (T-015) |
| `leakage_auditor` | LLM (`LLMNode`) | 1 — Understanding | Landed (T-014) |
| `baseline_designer` | LLM (`LLMNode`) | 3 — Baseline | Landed (T-020) |
| `baseline_runner` | Compute (`ComputeNode`) | 3 — Baseline | Landed (T-020) |
| `specialist_selector` | Compute (`ComputeNode`) | 5 — Implementation | Landed (T-023) |

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

`literature_researcher` and `web_researcher` (`src/nodes/llm/literature_researcher.py`,
`src/nodes/llm/web_researcher.py`, T-017) are the two nodes that populate the RAG store described
in § Tools → rag above. Both run in Pipeline Phase 2 (Research), in parallel per
`config/phases/phase2_research.yaml`'s `parallel_nodes` — the one sanctioned concurrent step in the
pipeline (CLAUDE.md invariant #6).

Each node:
1. Builds a search query from `state['problem_definition_path']`'s `problem_type` (re-relativized
   via `_research_common.relative_to_workspace`, falling back to a generic query when the path is
   empty or unreadable) plus `state['competition_name']`.
2. Calls an injectable `SearchClient` (`src/nodes/llm/_research_common.py`'s `Protocol`) defined
   locally in its own node module — no new `src/tools/` module was added for this. The production
   default is `LiteratureSearchClient` (arxiv + Semantic Scholar, stdlib `urllib.request` +
   `xml.etree.ElementTree`) for `literature_researcher`, and `WebSearchClient` (Tavily, stdlib
   `urllib.request`) for `web_researcher`.
3. Injects the raw search results into the prompt as a numbered `## Sources` block; the LLM
   extracts per-source `problem_type`/`methods_used`/`dataset_characteristics`/`key_findings`/
   `relevance_score` as a JSON array (`_research_common.extract_json_array` +
   `build_index_documents`), which is validated and zipped with the raw `SourceDocument`s into
   `IndexDocument`s.
4. Calls `RagStore(competition_name, ...).index(documents)` and writes a human-readable markdown
   report via `WorkspaceManager` (`reports/literature_research.md` / `reports/web_research.md`).

Neither node writes a `LabState` field — both run in the same Phase-2 super-step and `LabState`
fields other than `messages` have no LangGraph reducer, so two nodes writing the same key in one
super-step would raise `InvalidUpdateError` (see the OPEN discovery entry from T-002/T-009 in
`context/discoveries.md`). Both nodes' state delta is `{"messages": [...]}` only, inherited
unchanged from `LLMNode`'s base `_build_output_state`.

## Observability

### Layer 1 — Local JSONL logs (`src/observability/`)

`JsonlCallbackHandler(run_id, runs_dir=None)` (`src/observability/jsonl_callback.py`) is a
`langchain_core.callbacks.BaseCallbackHandler` subclass that appends one JSON line per node
entry/exit to `{runs_dir or REPO_ROOT/"runs"}/{run_id}/execution.jsonl`. `runs_dir` exists purely
for test injection, mirroring `src/graph/checkpointer.py`'s `build_checkpointer` convention.

- `run_id` is validated at construction (rejects empty, `.`/`..`, and any path separator) —
  raises `ValueError` immediately, since a malformed `run_id` reaching this constructor is a
  caller bug, not a runtime logging failure.
- Each line matches design.md § Observability's schema: `{timestamp, run_id, iteration, phase,
  node, event, duration_ms, tokens_in, tokens_out, model, output_summary}`. `event` is `"start"`
  or `"end"`; `duration_ms` is `null` on `"start"`, populated on `"end"`.
- `iteration` is read from the node's **input** state at `on_chain_start` time (LangGraph passes
  the node function's raw input — the full `LabState` dict — to this hook) and reused for the
  paired `"end"` line; it is *not* re-derived from `on_chain_end`'s `outputs`, since that's just
  the node's partial return delta and usually lacks this key.
- `phase` is derived from LangGraph's own `metadata["langgraph_checkpoint_ns"]` when available
  (the checkpoint namespace's first `":"`-delimited segment, before any `"|"`, is always the
  enclosing phase subgraph's stem — e.g. `"phase2_research:<uuid>|researcher:<uuid>"` ->
  `"phase2_research"`), falling back to `inputs.get("phase")` only when no LangGraph checkpoint
  namespace is present (e.g. a handler driven directly/standalone, as in this module's own unit
  tests). This is *not* simply `inputs.get("phase")`: `LabState["phase"]` (per
  `src/graph/builder.py`'s `_wrap_phase`) is only stamped with a phase name *after* that phase's
  subgraph finishes, so while `phase2_research`'s nodes are actually running, `inputs["phase"]`
  still holds the stale `"phase1_understanding"` value left over from the previous phase — reading
  the checkpoint namespace instead reports the phase whose subgraph is actually on the call stack
  at `on_chain_start` time, with no `LabState`/builder change required.
- `tokens_in`/`tokens_out`/`model` are populated by correlating `on_chat_model_start`/`on_llm_end`
  events (fired for the `self.llm.invoke(...)` call inside `LLMNode.__call__`, via LangChain's
  ambient `RunnableConfig` callback propagation — no wiring needed in `src/nodes/llm/base.py`)
  back to their owning node run via `parent_run_id`. For a node with no LLM call (any
  `ComputeNode`), these three fields are `null`, not `0` — `null` means "no LLM call observed",
  `0` would incorrectly imply a zero-token LLM call happened. The same `null` (not `0`) is reported
  when an LLM call *did* happen but its token usage couldn't be extracted from the response (e.g.
  `FakeListChatModel`, which never sets `usage_metadata`) — the per-node usage bucket tracks each
  token field as `None`-until-first-known-value rather than starting at `0`, so an unextractable
  call can't silently masquerade as "a real call that used zero tokens." Multiple real calls with
  known usage inside one node still sum correctly.
- `output_summary` is a best-effort, node-agnostic string: the last LLM message's `content` when
  the node's output includes a `messages` key, otherwise `"updated: {sorted output keys}"`;
  truncated to 200 characters.
- Only genuine `add_node`-registered graph nodes produce a log line. In this codebase's topology,
  `GraphBuilder.build()`'s outer graph invoke and each phase subgraph's own top-level `.invoke()`
  inside `_wrap_phase` (called with no `config` forwarded) also fire `on_chain_start`/`on_chain_end`
  — LangChain names these runs generically (`"LangGraph"`), not after any real node. They're
  filtered out via a positive signal, not a name blocklist: LangChain tags every chain run that
  occurs while a given graph node is executing with `metadata["langgraph_node"] = <node name>`,
  but only the node's *own* run has `name == metadata["langgraph_node"]` — the enclosing
  pregel-loop plumbing runs share that same `langgraph_node` value without matching it as their own
  `name`. A handler driven directly with no LangGraph context (`metadata` absent or missing
  `metadata["ls_integration"] == "langgraph"`, as in this module's own unit tests) is always
  treated as real, so this filter only ever suppresses runs proven to be LangGraph-internal.
- Logging never raises into the pipeline: any exception (bad path, write failure, unexpected
  callback shape) is caught in each overridden hook and reported as a one-line warning on stderr.
- **Not wired up yet** — no caller attaches this handler via `config={"callbacks": [handler]}` at
  a `graph.invoke(...)` call today; this task delivers the handler standalone, tested directly
  against LangChain's callback interface (including integration-style tests against a real
  LangGraph graph mirroring `_wrap_phase`'s exact wiring). Wiring it into `GraphBuilder`/an API
  entry point is a future task.
- **Known gap, not fixed here:** `src/graph/checkpointer.py`'s `build_checkpointer(run_id, ...)`
  still builds `{runs_dir}/{run_id}/checkpoint.db` without the same `run_id` validation (flagged
  in T-009's forward note) — out of this task's `src/observability/`-only scope.
- **Known limitation:** `output_summary` includes LLM message content near-verbatim (200-char
  truncated, no redaction) — see `context/decisions.md` for the latent secret-leak sink this could
  become once tool/subprocess output is fed through `LabState.messages` for LLM self-correction.

### Layer 2 — MLflow experiment tracking

> Skeleton — embedded MLflow tracking under `workspace/{competition}/mlruns/`, populated by the
> task that wires MLflow into training nodes.

### Layer 3 — LangSmith (opt-in)

> Skeleton — `LANGCHAIN_TRACING_V2=true` opt-in tracing, populated if/when a task wires it up.

## Invariants

> Skeleton — the pipeline-level invariants enforced by this codebase (fold immutability, single
> file-I/O point, best-score-only updates, retry caps, etc.), populated as each is implemented and
> enforced in code. See `design.md` § Critical invariants for the current target list.
