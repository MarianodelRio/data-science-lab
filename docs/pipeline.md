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
  on the current best. **Scores are normalized so that "higher is better" before being written
  to `last_score`/`best_score`** — the state contract itself has no polarity field, so
  `score_evaluator` (Pipeline Phase 6, `src/nodes/compute/score_evaluator.py`, T-031) is
  responsible for, and now implements, sign-flipping minimize-oriented metrics (RMSE, LogLoss,
  MAE, etc.) before writing — see "Evaluation (Phase 6)" above for the curated metric set and
  the separator-normalized matching this requires in practice.
- `current_iteration` is written by exactly one node: `experiment_designer` (Pipeline Phase 6,
  `src/nodes/llm/experiment_designer.py`, T-032), which runs **last** in that phase's sequence and
  increments it once per completed Phase 6 pass. The increment lives in `_build_output_state`, which
  `LLMNode.__call__` runs *after* `_resolve_output_path`, so every `{iteration}`-suffixed Phase 6
  artifact files under the **pre-increment** number and stays aligned with the `exp_{N}` directory
  just scored. Reordering that node would desync them. Safe against CLAUDE.md invariant #4 (baseline
  only at iteration 0) because the supervisor routes to `phase3_baseline` only from `phase2_research`
  at `current_iteration == 0`, while the Phase 6 loop-back goes to `phase4_design`; safe against
  concurrent writes because `current_iteration` is a plain `LastValue` channel and Phase 6 declares
  `parallel_nodes: []`.
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

### Design (Phase 4)

`config/phases/phase4_design.yaml`'s `sequence`: `solution_architect` → `feature_engineer` →
`analysis_critic`, critic targets `[solution_architect, feature_engineer]` (`max_retries: 3`),
`interrupt_after: true`. This phase is the **iteration loop head** — Phase 6 routes back here for
every iteration after the first (see "Supervisor" above), so unlike Phase 3's write-once baseline,
both artifacts are per-**iteration** paths under `design/iteration_{current_iteration}/` and a new
pair is produced on each pass.

- **`solution_architect`** (`src/nodes/llm/solution_architect.py`, `LLMNode` subclass, `model_role:
  reasoning`, T-021) — runs first, choosing the modeling strategy for this iteration.
  `_build_messages` queries the `RagStore` for modeling-strategy findings and reads
  `experiments/baseline/results.json` (from `state["baseline_results_path"]`), injecting both as one
  extra `HumanMessage`. `_write_output` extracts a JSON object using the same fence-stripping
  convention as `problem_framer`, then validates and writes `design/iteration_{iteration}/
  solution_plan.json` with exactly `model_families` (2-4 unique non-empty strings — normalized
  duplicates rejected), `order` (the same families as a permutation), `ensembling_strategy`,
  `realistic_ceiling` (`metric` / finite `target_score` / `rationale`) and `rationale`.
  `_build_output_state` sets `state["solution_plan_path"]`, which `feature_engineer` and every
  Phase 5 specialist read next. Two descopes are deliberate and human-approved — it reads no
  previous error diagnosis, and it never escalates to the `advisor` model role; its module
  docstring records both.
- **`feature_engineer`** (`src/nodes/llm/feature_engineer.py`, `LLMNode` subclass, `model_role:
  reasoning`, T-022, schema v2 in T-047) — runs second, designing the feature transformations that
  strategy needs. `_build_messages` injects the solution plan and the EDA report
  (`state["solution_plan_path"]`/`state["eda_report_path"]`) as one extra `HumanMessage`. Both are
  read through degrade-safe private helpers that catch `_experiment_design.DEGRADE_ERRORS` and
  guard a non-`str` path, so a partially-completed upstream phase — a missing artifact, a truncated
  one, a non-UTF-8 byte, a path recorded before the workspace moved — degrades to a placeholder
  string the prompt is told to read as "no information", and never aborts the graph run.
  `_write_output` extracts, validates and writes `design/iteration_{iteration}/feature_spec.json`
  (contract below). `_build_output_state` sets `state["feature_spec_path"]`, which is **load-bearing
  beyond its own value**: `analysis_critic._detect_phase_stem` uses its presence as the
  Phase-1-vs-Phase-4 discriminator rather than `state["phase"]` (which the graph stamps only *after*
  a subgraph finishes). That is sound only because the field is monotonic — once written it is never
  cleared — so `_build_output_state` must keep returning it (T-016).
- **`analysis_critic`** (`src/nodes/llm/analysis_critic.py`, `LLMNode` subclass) — the same node
  Phase 1 uses, with the same `pass`/`iterate` mechanics and the same forced `pass` after
  `max_retries` attempts (CLAUDE.md invariant #5). It reads each target's output as **raw text** via
  `_TARGET_STATE_FIELDS` (`solution_plan_path`, `feature_spec_path`) rather than parsing it, so it
  is schema-neutral: the v1 → v2 feature-spec migration required no change to it at all.

#### The `feature_spec.json` contract (v2)

`_validate_feature_spec` in `src/nodes/llm/feature_engineer.py` is a **whitelist rebuild**: the
written file has exactly one top-level key, `features`, and each entry is a fresh dict with exactly
these five keys in this order. The LLM's own object is never written through, so any extra key it
sends — including v1's `fold_aware`, `column`, `method`, `strategy` and `type` — is dropped.

| Key | Source | Contents |
|---|---|---|
| `columns` | LLM (validated) | non-empty list of non-empty column-name strings — one or many, no repeats |
| `operation` | LLM (validated) | non-empty free-form string naming the transformation, stripped |
| `params` | LLM (validated) | finite JSON scalars or flat lists of them; required even when `{}` |
| `fit_scope` | LLM (validated) | exactly `per_fold` or `global` |
| `rationale` | LLM (validated) | non-empty string, stripped |

Unlike `design.json`, nothing here is node-injected — every field comes from the LLM and is
validated, never supplied. `operation` and `rationale` are written **stripped**: both pass the
non-empty check with surrounding whitespace, and `coder` (T-029) string-matches `operation`.

**Two structural rules beyond the per-field ones.** A single entry may not name the same column
twice (`["amount", "amount"]` with `operation: "ratio"` is a constant-1 column, which reads
downstream as a modeling problem rather than as the specification bug it is — `solution_architect`
rejects normalized-duplicate `model_families` on the same reasoning). And no two entries may share
a `(columns, operation)` pair, since `coder` derives one column per entry and the pair is its name:
differing `params` change the values but not the name, so the check keys on the pair rather than on
full-entry equality. `columns` is ordered, so `["a","b"]` and `["b","a"]` are two different ratios
and remain two legal entries.

**One primitive, not three categories.** A per-column transform and a multi-column interaction are
the same shape: one entry with one column in `columns`, or one entry with several. There is no
minimum column count and no fixed catalogue of operations, which is what lets cyclical encoding,
log/power transforms, datetime-part extraction, aggregations, text length and outlier clipping be
expressed at all — none of them fit v1's `encodings`/`null_handling`/`interactions` split.

**`fit_scope` is required on every entry and has no default.** Matching is by exact token: neither
case-folded nor separator-normalized, so `"per-fold"`, `"PER_FOLD"`, `true`/`false` and a missing
key are all rejected. `coder` (T-029) branches on this value, so exactly two spellings may reach the
artifact.

**Leakage-prone operation families.** An operation whose name matches one of six families is fitted
on data and is therefore *required* to declare `per_fold`:

| Family | Representative recognized terms |
|---|---|
| Target encoding | `target_encoding`/`target_encode`/`target_encoder`, `target_mean`, `mean_encoding`, `leave_one_out`, `WOE`, `CatBoost`/`cat_boost` (`CatBoostEncoder`), `James-Stein`, `M-estimate`, `impact_encoding` |
| Statistical imputation | `median_impute`, `mean_imputation`, `mode_imputer`, `most_frequent_imputer`, `impute_median`, `fillna_median`, `median_fill`, `knn_impute`, `iterative_impute`, `simple_impute`/`simple_imputer`, `MICE` |
| Scaling / normalization | `standard_scale`, `min_max_scaler`, `minmax`, `robust_scale`, `max_abs_scale`/`maxabs`, `z_score`/`zscore`, `quantile_transform`, `power_transform`, `yeo_johnson`, `box_cox` |
| Binning / discretization | `quantile_bin`, `kbins`, `equal_width_bin`, `binning`, `discretize` |
| Dimensionality reduction | `pca`, `truncated_svd`, `umap`, `tsne`, `nmf`, `latent_dirichlet` |
| Frequency / count encoding | `frequency_encoding`, `count_encode`, `value_counts_encoding` |

Matching is **whole-phrase with word boundaries** against a normalized copy of `operation` —
`-`/`_` collapsed to spaces, case folded, and camelCase split — the mechanism T-022 introduced for
target encoding, generalized to six families in T-047. So `standard-scale`, `standard_scale`,
`Standard Scale` and `StandardScaler` all match, while an operation that merely contains a family
word (`log_transform`, `count_distinct_categories`, `mean_of_last_3_orders`) does not. No bare stem
(`scale`, `transform`, `normalize`, `standardize`, `encoding`, `impute`, `mean`, `count`) is ever a
keyword, for exactly that reason — `standardize`/`standardization` were briefly added during the
T-047 review and removed again in its second round, because "standardize" means "make uniform"
(`standardize_country_codes`, `standardize_text_case`) at least as often as it means z-scoring,
which `standard_scale`/`z_score`/`zscore` already cover.

The camelCase split exists because sklearn's own class names — `TargetEncoder`, `StandardScaler`,
`MinMaxScaler`, `KNNImputer`, `SimpleImputer` — are probable `operation` values and were reachable
by no keyword without it. An uppercase run followed by a capitalized word splits after the acronym
(`KNNImputer` → `knn imputer`, not `k n n imputer`), and a bare all-caps token is left whole
(`PCA` → `pca`). Matching runs against both the split and the unsplit normalization, so a keyword
carried concatenated in a tuple (`catboost`) still matches a camelCase spelling of the bare name
(`CatBoost` → `catboost`). It does **not** survive a suffix: `CatBoostEncoder` normalizes to
`cat boost encoder` and `catboostencoder`, and the concatenated keyword matches neither — which is
why the separated form (`cat boost`) is carried in the tuple alongside it.
An unseparated all-lowercase run (`targetencoder`) cannot be split by any rule that does not guess
word breaks, and matches nothing.

**Row-wise normalization is deliberately excluded** from the scaling family. `l2_normalize` /
`unit_norm` (sklearn's `Normalizer`) rescales each sample by its own norm and learns nothing from
any other row, so it is stateless and `global` is the *correct* declaration for it — the one the
prompt asks for. Flagging it made a conforming response raise, and `LLMNode.__call__` does not
catch `ValueError`, so a correct spec aborted the Phase 4 run.

**Severity is deliberately not uniform.** Target encoding is *target* leakage: the value for a row
is derived from the target column, so a CV score computed with it is not merely optimistic, it is
partly measuring the target. The other five leak only *feature* statistics out of the held-out fold
— a scaler's mean and σ, an imputer's median, a PCA basis, a category's global frequency — which
inflates the score more mildly. Rejecting both is a conservative stance, not a claim they are the
same bug.

That conservatism is **not symmetric**, which is the constraint governing every keyword choice. The
guard does not coerce `fit_scope` to `per_fold`; it raises `ValueError`, and neither
`LLMNode.__call__` nor any node wrapper in `src/graph/` catches it — so a false positive aborts the
Phase 4 run on a *correct* response. A false negative has three layers behind it: the prompt's
general "anything fitted is `per_fold`" rule, `code_critic`'s leakage rubric, and the remaining
keywords of the same family. Under-matching is a covered silent leak; over-matching is a dead run
with nothing behind it. A keyword therefore earns its place only when its whole phrase is
unambiguously the fitted technique and nothing else.

**Honest scope.** The family list is a floor, not the boundary — the same framing `FORBIDDEN_CV_KEYS`
carries. `operation` is an open vocabulary, so an operation matching no family gets no check and may
declare `global` even when it is genuinely fitted (`groupby_user_mean_amount` aggregates over other
rows and should be `per_fold`; nothing here catches it). Two things cover that gap:
`config/prompts/feature_engineer/v2.md` states the general rule prominently — *anything fitted, that
learns a parameter, statistic, mapping, vocabulary or basis from the data, must declare `per_fold`;
only a stateless row-wise transform may declare `global`* — and `code_critic`'s leakage rubric is
the downstream net that checks the generated code actually honors each entry's declared scope.

**Relationship to `design.json`'s `preprocessing`.** `preprocessing` (Phase 5) is a flat list of
lower_snake tokens naming steps; it has no way to express *when* a step is fitted, and
`FORBIDDEN_CV_KEYS` matches dict keys, never list values. The two vocabularies were aligned by
construction — `per_fold`/`global` are exactly the tokens
`deep_learning_specialist/v1.md`'s `standard_scaler_fitted_per_fold` convention already recommends —
so no rename is ever needed. **Where the two disagree, `feature_spec.json`'s `fit_scope` is
authoritative.** Widening `preprocessing` itself is a separate task (see the T-025 discovery, aimed
at T-029).

**v1 → v2.** The three fixed lists (`encodings`, `null_handling`, `interactions`) and the per-entry
`fold_aware` boolean are gone. `fold_aware: true` is now `fit_scope: "per_fold"`, and target
encoding is one family of six rather than the only guarded case. The prompt was bumped to `v2`;
`v1.md` is retained on disk, since `PromptLoader` is version-addressed and nothing enumerates the
directory (see `docs/agents.md` § Changing a prompt).

### Implementation (Phase 5)

`config/phases/phase5_implementation.yaml`'s `sequence`: `specialist_selector` → `coder` →
`code_critic`, critic targets `[coder]` (`max_retries: 3`), no interrupt. As of T-023, the YAML
deliberately does **not** list any of the 5 specialist names (`classical_ml_specialist`,
`deep_learning_specialist`, `nlp_specialist`, `timeseries_specialist`, `ensemble_specialist`) —
`specialist_selector` owns dispatching to exactly one of them internally (see "Supervisor" above),
so exactly one specialist design is produced per iteration. Listing them in the phase YAML too
would execute every one of them a second time, unconditionally (see `docs/agents.md` § Adding an
agent, step 3).

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
  retry loop. All five specialists — `classical_ml_specialist` (T-024),
  `deep_learning_specialist` (T-025), `nlp_specialist` (T-026), `timeseries_specialist` (T-027)
  and `ensemble_specialist` (T-028) — have now landed and resolve to their real nodes; the
  `NoOpNode` fallback `resolve_node` documents for "not implemented yet" is no longer reachable
  via any name `specialist_selector` can select.

- **`classical_ml_specialist`** (`src/nodes/llm/classical_ml_specialist.py`, `LLMNode` subclass,
  `model_role: reasoning`) — `specialist_selector`'s *default* route, for tabular problems.
  `_build_messages` injects three sections as an extra `HumanMessage`: `## Solution plan` (read
  from `state["solution_plan_path"]`), `## Frozen CV folds` (`strategy`/`n_folds`/`seed` only, read
  from `state["validation_config_path"]` — never `fold_indices`, which would flood the context with
  per-row index data), and `## Feature spec reference` (the workspace-relative path only, not the
  file's contents). Each degrades to a `(... not yet available)`/`(unable to read ...)` placeholder
  rather than raising, so Phase 5 remains invokable standalone. It also stashes the resolved
  feature-spec reference on the instance for `_write_output`, because `LLMNode.__call__` never
  passes `state` to `_write_output` (same mechanism as `literature_researcher`'s `self._sources`);
  an unresolved stash raises rather than writing a wrong pointer. `_write_output` extracts a JSON
  object from the response, validates it against the shared `design.json` contract below, and writes
  `experiments/exp_{iteration}/design.json` via `workspace.write_json`. It picks one `model_family`
  out of `xgboost`/`lightgbm`/`catboost`/`extra_trees` — normalized to the canonical token by
  word-boundary alias matching (`xgb`, `LGBM`, `light-gbm`, `ExtraTrees`, ...), with an **ambiguous**
  response naming two families (`"xgboost or lightgbm"`) rejected rather than resolved by
  precedence, since `coder` dispatches on that value. It does **not** override
  `_build_output_state` — `coder` reads `design.json` back from its well-known workspace path, the
  same convention `baseline_designer`/`fold_config.json` use, so there is no new `LabState` field.

- **`deep_learning_specialist`** (`src/nodes/llm/deep_learning_specialist.py`, `LLMNode` subclass,
  `model_role: reasoning`) — reached when the plan's signal is neural (`neural`, `cnn`, `rnn`,
  `deep learning`, `pytorch`, `lstm`). Structurally identical to `classical_ml_specialist` above:
  the same three injected sections, the same instance-stash mechanism for the feature-spec
  reference, the same shared validator, the same output path, and no `_build_output_state` override.
  It differs in carrying **its own** family table — `tabnet`, `node` (Neural Oblivious Decision
  Ensembles) and `mlp` — which is possible without touching the shared module because
  `normalize_model_family` takes the table as a *parameter*; each specialist contributes its own
  rather than extending a common one.

  Two neural-specific requirements live in its prompt rather than in the validator, because neither
  is expressible in the current schema, and **neither is validator-enforced**:
  1. *Fit scope.* Neural nets need fitted preprocessing (scalers, imputers), but `preprocessing` is
     a flat token list with no fit-scope notion, and `FORBIDDEN_CV_KEYS` matches dict *keys*, not
     list *values* — so a scaler fitted before the CV split is silent feature-statistic leakage
     across the frozen folds. The prompt requires fitting inside each fold and recommends making it
     visible in the token itself (`standard_scaler_fitted_per_fold`). See `context/discoveries.md`
     for the hand-off to `coder` (T-029).
  2. *Scalar-only architecture parameters.* `choices` accepts only JSON scalars, so tuning over
     layer-width tuples (`[[64,32],[128,64]]`) is rejected. The prompt decomposes the architecture
     into `n_layers`/`layer_width`/`width_decay`/`embedding_dim_multiplier`. Note the asymmetry:
     `fixed_params` *does* accept a flat list of scalars, so one fixed `hidden_dims` is legal.

  The task's "activated only when the dataset is large enough" condition is likewise prompt-level:
  `LabState` carries no row count or shape, and `specialist_selector` has already routed the
  iteration here by the time the node runs, so the prompt degrades capacity (a modest MLP rather
  than TabNet) and records the concern in `rationale` instead of refusing.

- **`nlp_specialist`** (`src/nodes/llm/nlp_specialist.py`, `LLMNode` subclass, `model_role:
  reasoning`) — `specialist_selector`'s route for text-heavy problems (routed via `_NLP_KEYWORDS`,
  T-023). Structurally a mirror of `classical_ml_specialist`: `_build_messages` injects the same
  three sections (`## Solution plan`, `## Frozen CV folds`, `## Feature spec reference`) as an extra
  `HumanMessage`, each degrading to a placeholder rather than raising. Unlike
  `classical_ml_specialist`, which keeps its own node-local `_read_solution_plan` copy, this node
  uses the shared `read_solution_plan` now hoisted into `_experiment_design.py` (T-026 is the third
  copy of that reader, meeting the hoist threshold the T-024 decision log pre-approved). It also
  stashes the resolved feature-spec reference on the instance for `_write_output`, and
  `_write_output` extracts and validates the JSON payload the same way before writing
  `experiments/exp_{iteration}/design.json` via `workspace.write_json`. It picks one `model_family`
  out of `tfidf_linear`/`sentence_embeddings`/`transformer_finetune` — normalized to the canonical
  token by word-boundary alias matching (`TF-IDF`, `SBERT`, `DistilBERT fine-tuning`, ...), with an
  **ambiguous** response naming two families rejected rather than resolved by precedence. It does
  **not** override `_build_output_state`, for the same reason `classical_ml_specialist` does not.

- **`timeseries_specialist`** (`src/nodes/llm/timeseries_specialist.py`, `LLMNode` subclass,
  `model_role: reasoning`) — `specialist_selector`'s route for temporal/forecasting problems (routed
  via `_TIMESERIES_KEYWORDS`, the *first* of its four keyword branches, T-023). Structurally a
  mirror of `nlp_specialist`: the same three injected sections, the shared `read_solution_plan`/
  `read_fold_summary`/`resolve_feature_spec_ref` helpers, the same instance stash for the
  feature-spec reference, the same shared validator, the same `experiments/exp_{iteration}/design.json`
  output path, and no `_build_output_state` override. It picks one `model_family` out of `arima`,
  `prophet`, `exponential_smoothing`, `gradient_boosting_lags` and `linear_lags` (its own five-family
  table, again passed to `normalize_model_family` as a parameter). Its aliasing is generous where
  that is safe and deliberately absent where it is not — `specialist_selector`'s routing vocabulary
  is almost disjoint from these tokens, and an unresolvable `model_family` is a hard `ValueError`
  that aborts the phase with no artifacts, but a *wrong* resolution is worse than a loud one:

  - *Concatenated spellings are listed explicitly*, because normalization collapses `-`/`_` to a
    space but never splits CamelCase: `\barima\b` cannot reach inside `sarimax`/`autoarima`, and
    `ExponentialSmoothing` (statsmodels' own class name for one of these families),
    `GradientBoostingRegressor`, `XGBRegressor` and the CamelCase rendering of the canonical tokens
    are all unreachable from their spaced twins.
  - *A bare "linear" is not aliased.* It is a trend word far more often than a family word here, and
    it co-occurs with every other family — "Holt's linear trend method", "Prophet (growth=linear)",
    "ARIMA with linear trend", "LightGBM linear_tree" — so aliasing it made all of those ambiguous.
    Only the qualified forms (`linear lags`, `linear regression`, `linear model`) are aliased.
  - *Bagged trees are not aliased to boosting.* `random forest`/`extra trees`/`decision tree` raise
    "not a supported model family" rather than silently resolving to `gradient_boosting_lags`, which
    would hand `coder` a boosting model with `bootstrap`/`oob_score` hyperparameters and a
    contradicting `rationale`. Rejecting is the safe direction to fail — the same principle
    `deep_learning_specialist` documents.
  - *A bare "lag features" is aliased to neither lag family*, since both are models over lag
    features — the model brand alone discriminates.

  Three rules live in its prompt rather than in the validator, and **none is validator-enforced**:
  1. *No self-gate on temporal structure.* The task's "activated only when temporal structure exists"
     is satisfied upstream: `specialist_selector` is the sole gate and nothing is queued behind this
     node, so thin or absent temporal evidence degrades the design (a short-lag `linear_lags`/
     `gradient_boosting_lags` fallback) and is recorded in `rationale` — never a refusal.
  2. *Never uses future data.* Fit scope is not expressible in `design.json` — `preprocessing` is a
     flat token list and `FORBIDDEN_CV_KEYS` matches dict *keys*, not list *values* — so centered
     windows, negative shifts and pre-split statistics cannot be detected. The prompt requires
     past-only features and makes the requirement visible in the token itself
     (`rolling_mean_past_only`). Note also that the frozen strategy may legitimately *not* be
     time-aware (`stratified_kfold` on a forecasting problem): the folds are write-once, so the node
     designs against them and notes the mismatch in `rationale` rather than changing them.
  3. *Column identity comes from `feature_spec_ref`.* The node is given the feature spec's path, never
     its contents, so the prompt forbids inventing a time/date/target column name.
     `_FOLD_SUMMARY_KEYS`/`read_fold_summary` are deliberately **not** widened to carry one — that
     would stale the three landed sibling prompts — and there is no node-local fold reader here.

  Tuple-shaped hyperparameters (ARIMA `order` `(p, d, q)`, `seasonal_order` `(P, D, Q, s)`) follow
  `nlp_specialist`'s `ngram_range` precedent: hyphenated string tokens (`"1-1-1"`) either as
  `categorical` `choices` or pinned in `fixed_params`. The array form is *enforced* only in
  `search_space` — `choices` accepts scalars only — whereas `_validate_fixed_params` explicitly
  permits a flat list of scalars, so `fixed_params: {"order": [1, 1, 1]}` passes validation. Banning
  it there is a **pipeline convention stated in the prompt**, not a schema rejection, so that `coder`
  (T-029) has one encoding to parse rather than two; the string convention itself is unvalidated
  (see `context/discoveries.md`).

- **`ensemble_specialist`** (`src/nodes/llm/ensemble_specialist.py`, `LLMNode` subclass,
  `model_role: reasoning`, T-028) — `specialist_selector`'s route once `>= 2` prior experiments
  exist and `solution_plan.json`'s `ensembling_strategy` doesn't say "no ensembling"
  (`_should_ensemble`, checked independently of the 4-branch keyword precedence above). Like its
  four siblings it injects `## Solution plan`, `## Frozen CV folds` and `## Feature spec
  reference` as part of the same extra `HumanMessage`, using the shared `read_solution_plan`/
  `read_fold_summary`/`resolve_feature_spec_ref` helpers and the same instance-stash mechanism for
  the feature-spec reference — but it also injects a fourth section, `## Base experiments
  (out-of-fold predictions)`, that none of the other four specialists have.

  **`base_experiments` is node-injected, never read from the LLM response** — the same convention
  `feature_spec_ref`/`cv_strategy_ref` already use. `_build_base_experiments` walks **every**
  entry of `state["experiments"]`, in order (never filtered, reordered, or influenced by the
  LLM), and resolves for each one:
  - `experiment_id` — the entry's own `id` when it's a non-empty string, else `experiment_{i}`.
  - `oof_path` — the entry's own experiment directory's `results.json`, using its `oof_path`
    field when present and it re-relativizes cleanly (rejecting `..`/absolute-outside-workspace),
    else falling back to the well-known `oof_predictions.parquet` file in that same directory.
    Binding on whoever writes `results.json` (`coder`, T-029) — see `context/discoveries.md`.

  The experiment directory itself is resolved per entry (`_experiment_dir_from_entry`) by the same
  normalization `code_critic._experiment_dir_from_state` applies to `state["experiments"][-1]`
  — relativize, a value with a suffix is treated as a file pointer and its parent taken, reject
  `..` — duplicated locally rather than imported, because sibling LLM node modules never import
  from each other (`context/decisions.md` T-022/T-024/T-025). It falls back to
  `experiments/exp_{iteration}` (the entry's own recorded `iteration` when usable, else its list
  position) whenever the entry's `path` is missing, absolute-outside-workspace, or contains `..`.

  This node never self-gates on ensembling eligibility either: `specialist_selector` is the sole
  gate, and by the time this node runs the `>= 2` decision has already been made. The shared
  schema itself only requires a **non-empty** `base_experiments` (mirroring `search_space`'s "must
  not be empty" floor) — an ensemble over zero sources is unrepresentable, but a single-source one
  is degenerate-yet-representable, and re-deriving the `>= 2` eligibility rule here would duplicate
  `specialist_selector._should_ensemble`'s routing decision.

  It picks one `model_family` out of `stacking`, `blending` and `weighted_average` — its own
  three-family table, again passed to `normalize_model_family` as a parameter. Deliberately no bare
  `"weighted"`, `"weight"`, `"stack"` or `"stacked"` alias — only qualified multi-word forms — the
  same "bare generic modifier not aliased" technique `timeseries_specialist` uses for a bare
  `"linear"`. This defuses "weighted blend of stacked models" to `blending` alone, but it cannot
  defuse "blended stacking": both "blended" and "stacking" are real canonical self-match aliases of
  different families, so that phrase is structurally ambiguous by design and the prompt states the
  rejection explicitly (`context/decisions.md`, cross-referencing the T-026 "no longest-match-wins"
  discovery). A pure equal-weight average is unrepresentable — `search_space` must be non-empty —
  so the prompt requires tuning per-source weights (or a temperature) for `weighted_average`, or the
  meta-learner's hyperparameters for `stacking`/`blending`. Per-source weight parameter names use
  the **positional index** in `## Base experiments` (`weight_0`, `weight_1`, ...), never the raw
  `experiment_id`, which may not be a valid Python identifier.

  **Reachable now that `coder` (T-029) lands.** `coder` is the sole writer of
  `state["experiments"]` — once two real iterations have each appended an entry,
  `specialist_selector`'s `>= 2` check can be satisfied in a real run and this node is dispatched
  to for real. It remains additionally exercised in tests against a directly-constructed `state`,
  the same scoping `code_critic` (T-030) documents for its own well-known-path reads.

- **`coder`** (`src/nodes/llm/coder.py`, `LLMNode` subclass, `model_role: implementation`, T-029)
  — the phase's **only** node that writes ML implementation code, running between
  `specialist_selector`'s chosen specialist and `code_critic`. Like `code_critic` it overrides
  `LLMNode.__call__` **wholesale** rather than composing via the `_build_messages`/`_write_output`/
  `_build_output_state` hooks, because it owns its own execute-then-re-prompt retry loop — the same
  precedent T-009 established for `code_critic`.

  **Inputs.** One `HumanMessage` with four labeled sections: `## Experiment design (design.json)`
  (read from `experiments/exp_{iteration}/design.json`, degrading to a placeholder on
  `_experiment_design.DEGRADE_ERRORS`), `## Feature spec (feature_spec.json)` (resolved via the
  shared `resolve_feature_spec_ref`, degrading the same way), `## Frozen CV folds` (the shared
  `read_fold_summary` — a `strategy`/`n_folds`/`seed` summary only; the prompt requires the
  generated script to load `validation/fold_config.json` itself at runtime rather than trust this
  summary), and `## Run configuration` (the literal `optuna.n_trials`/
  `optuna.early_stopping_patience`/`workspace.mlflow_tracking_uri` values from `Settings.load()`,
  read directly in `__call__` — a **second**, module-local `Settings.load()` call alongside the one
  the inherited `LLMNode.__init__` already makes for `_max_messages_per_node`).

  **Execution-retry loop.** For up to `1 + _MAX_EXECUTION_RETRIES` (`_MAX_EXECUTION_RETRIES = 2`,
  so 3 attempts total) cycles: invoke the LLM, extract the single fenced ```` ```python ```` block
  (`_extract_code` — raises on zero or more than one fenced block, or an empty one), write it to
  `experiments/exp_{iteration}/train.py` via `WorkspaceManager.write_text` (overwriting whatever an
  earlier attempt wrote — see "the experiment-directory-overwrite-on-retry convention" below),
  execute it for real via `code_executor.execute(code, cwd=workspace.workspace_path)`, and run
  `_validate_run` — a single failure gate covering both subprocess-level failure (`timed_out`,
  nonzero `returncode`) and artifact-level failure (`results.json` unreadable/not-an-object,
  `cv_score` missing/non-numeric/non-finite, an out-of-vocabulary `metric`, a missing
  `submission.csv`, a missing OOF artifact). On any failure short of the last attempt, the failure
  reason plus the subprocess's `stderr` are appended as a new `HumanMessage` (`_failure_message`)
  and the loop re-prompts; unlike `code_critic`'s separate design-quality loop, there is **no
  forced-pass** here — exhausting the budget raises `ValueError` and the graph run fails loudly.

  **Critic-feedback threading.** `code_critic` re-invokes `coder` directly
  (`node_resolver.resolve_node("coder")(working_state)`) with its own verdict `AIMessage` already
  appended to `working_state["messages"]`. `coder.__call__`'s first step —
  `trim_context(state.get("messages", []), self._max_messages_per_node)` — picks that message up
  and threads it into the outgoing prompt, so critic feedback reaches the LLM with no extra
  plumbing between the two nodes.

  **Output contract.** The generated script must write, workspace-relative from its own subprocess
  cwd: `{exp_dir}/results.json` (`cv_score: float` required; optional `metric` — one of
  `accuracy`/`r2`/`rsquared`/`score`, matched case/separator-insensitively; optional
  `feature_importance`+`feature_names` for tree-ensemble families; `oof_path` pointing at the OOF
  file), `{exp_dir}/submission.csv`, and `{exp_dir}/oof_predictions.parquet` (the fixed fallback
  filename `_oof_artifact_exists` falls back to checking when `results.json` carries no usable
  `oof_path`).

  **`fit_scope` v2, dispatch-free.** For every `feature_spec.json["features"]` entry the prompt
  requires branching on `fit_scope`: `"per_fold"` fitted inside the CV loop on the training fold
  only and applied unchanged to validation/test; `"global"` applied once outside the loop. There is
  no fixed operation → code dispatch table in the prompt or in this node — the LLM writes the
  pandas/sklearn code for each `operation`+`params` pair directly, guided by `rationale`, and the
  prompt requires an explicit `raise` naming any `operation` it does not recognize, never a silent
  skip. A missing/unreadable `feature_spec.json` degrades to raw feature columns unchanged, per
  `_read_feature_spec`.

  **Column safety.** `feature_spec.json`'s `columns` values are free strings copied verbatim from
  real dataset headers and may contain quotes, backslashes, newlines or `#`. The prompt requires
  every column name that must become Python source text to go through `repr()` or `json.dumps()` —
  never string-concatenated into a quoted literal or a `df.query(...)` string — with a worked
  example mirroring `code_critic`'s worked JSON example.

  **`design.json` dispatch and the `FORBIDDEN_CV_KEYS` gaps.** The prompt requires dispatching on
  `model_family` with a `raise ValueError` naming any unrecognized family, defaulting
  `gradient_boosting_lags` to the already-installed `lightgbm`, parsing ARIMA-style
  `order`/`seasonal_order` defensively (a hyphenated string or a flat list of scalars — never a bare
  `int(part)`), and — since the shared design validator matches `FORBIDDEN_CV_KEYS` by exact,
  case-sensitive key name and does not police model-side holdout/fold-shaping hyperparameters at
  all (`validation_fraction`, `early_stopping`/`n_iter_no_change`, `eval_set`/
  `early_stopping_rounds`, `od_type`/`od_wait`, `validation_split`/`val_size`, `gap`,
  `max_train_size`, `initial`, `horizon`, `period`, `cutoffs`) — deciding per model family whether
  to honor each one against the frozen fold's own validation split or drop it, and recording that
  decision in `results.json` (e.g. `"holdout_param_handling"`). `forecast_horizon` is a legitimate
  model parameter and is always honored. `torch`, `pytorch-tabnet`, `statsmodels`, `prophet` and
  `transformers` are not installed; the prompt requires preferring an already-installed alternative
  where one exists and otherwise importing the real library anyway and letting the resulting
  `ImportError` surface as a normal execution failure rather than fabricating a result.

  **Test patch points.** This custom `__call__` instantiates `WorkspaceManager` directly in this
  module, so unit tests patch `src.nodes.llm.coder.WorkspaceManager` — except the module's own test
  file uses a **real** `WorkspaceManager` against `tmp_path`, since `_validate_run`/
  `_oof_artifact_exists` do real `Path.exists()` filesystem checks that a mock cannot satisfy
  transparently. `Settings` is patched at *both* `src.nodes.llm.base` (the inherited `__init__`'s
  `_max_messages_per_node` read) and `src.nodes.llm.coder` (the `__call__`-local
  `optuna`/`mlflow` read). `execute` is patched at `src.nodes.llm.coder`.

  **Deliberate scope gap — no consolidated `src/` files.** `coder` writes only the per-experiment
  `experiments/exp_{iteration}/train.py` (plus `results.json`/`submission.csv`/OOF predictions). It
  does **not** write consolidated workspace-root `src/features.py`/`src/models.py`/`src/train.py`,
  which `spec.md`'s Phase 5 Interface section and `reviewer`'s (T-033) pinned candidate list both
  mention as eventual `coder` outputs. This was a human-approved deferral at the T-029 Phase 2
  checkpoint, not an oversight: `coder`'s generated scripts are self-contained per experiment
  (inline feature engineering, no importable `features.py` module to extract), and `reviewer`
  already degrades each missing candidate to "(not present in the workspace)" within its shared
  injection budget, so nothing crashes without them — the review it produces is simply weaker.
  See `context/discoveries/T-029.md` for the open follow-up (unassigned — a future task must decide
  whether `coder` or a Phase 7 node materializes these files, and whether they make sense at all).

- **`code_critic`** (`src/nodes/llm/code_critic.py`, `LLMNode` subclass, `model_role:
  implementation`, T-030) — the phase's **last** node, and its critic (`critic: {node: code_critic,
  targets: [coder], max_retries: 3}` in `config/phases/phase5_implementation.yaml`). Like
  `analysis_critic` it overrides `LLMNode.__call__` **wholesale** rather than composing via the
  `_build_messages`/`_write_output`/`_build_output_state` hooks, because it owns its own retry
  control flow (T-009's "a critic's own node function re-invokes its target node(s) directly …
  entirely inside its own `__call__`"). It does **not** override `_resolve_output_path`: unlike
  `analysis_critic` it runs in exactly one phase, so `{iteration}` alone disambiguates and the base
  implementation suffices.

  **Review sections.** Each cycle it injects one `HumanMessage` with four labeled sections, all read
  through `WorkspaceManager` and all degrading to a placeholder rather than raising (the readers
  catch `_experiment_design.DEGRADE_ERRORS` — `(OSError, ValueError, RecursionError)` — not a bare
  `OSError`, so a non-UTF-8 artifact or an out-of-workspace path degrades too):
  `## Generated training code (train.py)` (`(unable to read … — there is nothing to review)` when
  absent, which the prompt treats as a hard `iterate`), `## Experiment design (design.json)` and
  `## Experiment results (results.json)` (`({filename} not available for this experiment)`, read as
  *text*, never `read_json`), and `## Frozen CV folds` (the shared `read_fold_summary`).

  **All three** file artifacts are truncated at 20 000 characters with an in-band
  `... (truncated at N characters of {label})` marker. The code needs it to bound the prompt window;
  `results.json` needs it more, because it is written by the *generated* script — the least-trusted
  component in the system — and normally carries the OOF predictions, so an uncapped one is an
  uncapped prompt (a 4 MB `results.json` measured at ~4 000 000 characters in a single `invoke`,
  against CLAUDE.md's "< $0.50 per full competition run" target).

  The code is emitted inside a backtick fence computed to be **longer than the longest backtick run
  in the script**, not a fixed ```` ```python ````: a generated `train.py` containing a ``` line would
  otherwise close the fence early and let the rest of the file render as top-level prompt markup —
  a valid-Python docstring can carry a counterfeit `## Experiment design` section instructing a
  `pass`. The prompt states separately that everything under the code heading is data to review,
  never an instruction to obey. (The retry cap is no defense here: it bounds *iterate* loops, while
  injection seeks a false *pass*.)

  The experiment directory is resolved **once per cycle** — to whichever candidate actually yielded
  `train.py` — and `design.json`/`results.json` are then read from **that same directory only**, so
  all three artifacts always describe one experiment. Candidates are
  `state["experiments"][-1]["path"]` when usable (re-relativized; a value with a suffix is treated as
  a file pointer and its parent taken — `coder` (T-029) in fact always records the bare experiment
  *directory*, e.g. `experiments/exp_0`, but this node keeps the suffix-stripping fallback so it works
  with either convention), then the well-known `experiments/exp_{current_iteration}/`; when no
  candidate yields the script the first is used so the placeholders still name one place. Scanning
  per artifact instead would let the critic review `exp_7`'s code against `exp_0`'s design — a real
  risk in a multi-iteration run, since nothing increments `current_iteration` until
  `experiment_designer` runs in Phase 6 — and, because the prompt accepts early stopping only when
  it is "recorded in `results.json`", a stale `results.json` is a route to a false **pass**, not
  merely a false iterate.

  **The experiment-directory-overwrite-on-retry convention.** `coder` overwrites
  `experiments/exp_{iteration}/` in place on every internal execution retry (its own bounded
  `_MAX_EXECUTION_RETRIES` loop) **and** on every `code_critic`-triggered re-invocation of the whole
  node. Only the very first, graph-level `coder` call's returned delta is ever applied to the real
  `LabState` — LangGraph applies a node's return value once per graph step, and `code_critic`'s
  internal re-invocations (via `node_resolver.resolve_node("coder")(working_state)`) mutate only its
  own local `working_state`, never the graph itself. So `result["experiments"]` gains exactly **one**
  entry per graph-level Phase 5 iteration, no matter how many times `coder`/`code_critic` looped
  internally — the on-disk `train.py`/`results.json`/`submission.csv`/OOF artifacts, by contrast, end
  up holding whatever the *last* internal retry wrote.

  **Retry contract.** The budget is read from `load_phase_config("phase5_implementation").critic
  .max_retries` — *not* `Settings.execution.max_critic_retries` (both are `3` today; the phase YAML
  is the contract that also names the critic and its targets, and it allows a per-phase budget).
  Counts are kept **per target**; on the `max_retries + 1`-th `iterate` for a target the node
  appends a `forced_pass: True` record and breaks (CLAUDE.md invariant #5). A global
  `(max_retries + 1) * max(len(targets), 1)` cycle cap wraps the loop as defense-in-depth. That cap's
  `for...else` branch is unreachable for **any input the LLM can currently produce** (the same
  pigeonhole argument `analysis_critic` uses, and here there is only one target), but it is *not*
  unreachable in general: `load_phase_config` does not validate `max_retries`, so a phase YAML
  carrying a negative value makes the budget `<= 0`, the loop body never runs, and the node emits a
  forced pass having made zero LLM calls and reviewed nothing. The branch therefore references no
  loop-local name and records `code_available: None`. The unvalidated field is flagged to the
  `src/config/` owner in `context/discoveries.md`.

  The target is re-invoked as `node_resolver.resolve_node(target)(working_state)` — bound through the
  **module attribute**, matching `src/graph/builder.py` and deliberately not `analysis_critic`'s
  import-time form, which B-001 showed to be import-order fragile. There is **no** `try/except` around
  that call: `coder` has no write-once exception (`FoldsAlreadyFrozenError` is
  `validation_strategist`-specific), so a real crash in the target must surface.

  A malformed response, an unknown `verdict` value or a blank `feedback` all normalize to `iterate`
  with non-empty synthesized feedback. Parsing starts at `_experiment_design.extract_json_object`
  (fence stripping plus first-`{`-to-last-`}` salvage) rather than another private fence-stripper
  copy, but that alone is not enough here: when a trailing fenced block **contains braces** the
  salvage window overshoots the real object and fails, and for a *code* critic the likeliest postamble
  of all is a Python snippet illustrating the fix. So `_extract_verdict_data` falls back to retrying
  on the prefix before each fence marker — without which an `iterate` lost its `feedback` to the
  "could not parse" text (re-invoking `coder` with no signal and burning a retry) and a `pass` became
  a spurious `iterate`. The whole parse is guarded by `DEGRADE_ERRORS`, not a bare `ValueError`,
  because `json.loads` raises **`RecursionError`** on a deeply nested payload (reproducible at ~2 400
  characters, i.e. within this agent's token budget) and letting that escape would abort the graph run
  from the one node whose contract is to degrade — before any verdict record was written.

  **State delta.** `{"messages": [...]}` **only** — no new `LabState` field. `src/state.py` is a
  protected contract and `experiments` has no writer in `src/` yet, so the generated code is located
  at a well-known workspace path (the `design.json` precedent) instead. The retried target's
  non-`messages` delta *is* merged into the node-local `working_state` — that is what lets the next
  review cycle re-read the regenerated `train.py` when `coder` moves the recorded path — but it is
  never returned.

  **Output.** `workspace.write_json(self._resolve_output_path(state), {...})` (the *original*
  `state`, so a target delta cannot move the record path mid-loop) writes
  `reports/code_critic_verdicts_iter{iteration}.json` with `phase`, `targets`, `attempts` (one
  record per cycle: `verdict`, `feedback`, `target_node`, `code_available`, plus `forced_pass` on a
  forced record) and `final_verdict` (the last attempt). Caveat: nothing in `src/` increments
  `state["current_iteration"]` yet (see `context/discoveries.md`), so today every iteration of a run
  writes `…_iter0.json` and the later write wins. Documented, not fixed here — a `current_iteration`
  writer is a `src/graph/` change owned by the evaluation-phase tasks.

#### The `design.json` contract (shared by all Phase 5 specialists)

`src/nodes/llm/_experiment_design.py` is the single source of truth for the shape of
`experiments/exp_{iteration}/design.json` — imported by all five landed Phase 5 specialists
(`classical_ml_specialist` T-024, `deep_learning_specialist` T-025, `nlp_specialist` T-026,
`timeseries_specialist` T-027, `ensemble_specialist` T-028) and read by `coder` (T-029). Like
`_research_common.py`, it declares no class whose `name` equals its own module stem, so
`resolve_node` never mistakes it for a node module.

`validate_experiment_design` is a **whitelist rebuild**: it returns a fresh dict with exactly these
eight keys, in this order, and the LLM's own object is never written through.

| Key | Source | Contents |
|---|---|---|
| `specialist` | **node-injected** | the specialist's own `name` |
| `model_family` | LLM (normalized) | one canonical family token |
| `search_space` | LLM (validated) | non-empty map of Optuna parameter specs |
| `fixed_params` | LLM (validated) | scalars / flat lists of scalars; required even when `{}` |
| `preprocessing` | LLM (validated) | list of lower_snake tokens matching `^[a-z][a-z0-9_]{0,63}$`; required even when `[]` |
| `rationale` | LLM (validated) | non-empty string |
| `feature_spec_ref` | **node-injected** | workspace-relative path of the feature spec |
| `cv_strategy_ref` | **node-injected** | always `validation/fold_config.json` |

Any other top-level key the LLM sends — including `n_trials` and `early_stopping_patience` — is
dropped by the rebuild: the trial budget is a pipeline-wide setting (`config/settings.yaml`'s
`optuna:` block), never per-experiment.

The output path is per-**iteration**, not per-**specialist**: all five specialists write the same
`experiments/exp_{iteration}/design.json`. That is sound because `specialist_selector` activates
exactly one specialist per iteration (design.md invariant #7) and dispatches to it exactly once, and
it keeps `coder`'s read at a fixed well-known path with no directory globbing and no new `LabState`
field. If a future task ever runs two specialists in one iteration, it changes the pattern for all
five at once — see the T-025 entry in `context/decisions.md`.

**Cross-validation may not be redefined.** Before anything else, the validator rejects — loudly,
naming the key and where it appeared — any of `cv`, `cv_strategy`, `folds`, `fold_indices`,
`n_folds`, `n_splits`, `validation`, `test_size`, `shuffle` appearing at the top level or as a key
inside `search_space`/`fixed_params`. Matching is by **exact key name**, never substring, which is
why the injected `cv_strategy_ref` is not itself caught. Rejecting rather than silently dropping is
what makes "the design references the frozen folds and does not redefine CV" (CLAUDE.md invariant
#1) an assertable behavior instead of a vacuous consequence of the whitelist.

**Search-space grammar.** Every `search_space` value must be an object with a `type` of `int`,
`float` or `categorical` — an expression string (`"trial.suggest_float(...)"`), a distribution-call
string (`"loguniform(1e-3,1e-1)"`) and a bare 2-tuple (`[0.001, 0.1]`) are all rejected with an
explicit message. `int`/`float` require finite `low` < `high` (booleans rejected explicitly, since
`isinstance(True, int)` is `True`; `int` requires integer bounds); optional `log` must be a real
`bool`, requires `low > 0`, and may not be combined with `step` (Optuna raises on that combination
at `suggest_*` time, so it fails here instead); optional `step` must be finite and `> 0`, and an
integer for `int` parameters. `categorical` requires a non-empty `choices` list of JSON scalars with
no duplicates and no nested objects/lists. Unknown inner keys are dropped silently. Finally, a
parameter name may not appear in both `search_space` and `fixed_params`.

**`ensemble_specialist` alone extends the contract with a ninth key.** `ENSEMBLE_DESIGN_KEYS =
DESIGN_KEYS + ("base_experiments",)`, and `validate_ensemble_design` is a thin wrapper around
`validate_experiment_design` — it calls the eight-key validator unchanged and then appends a
whitelist-rebuilt `base_experiments` (a non-empty list of `{"experiment_id", "oof_path"}`,
node-injected from `state["experiments"]`, never read from the LLM response) as the ninth and
last key. The other four specialists' eight-key `validate_experiment_design` path, and `coder`'s
(T-029) reads of their `design.json` files, are entirely unaffected by this addition.

### Evaluation (Phase 6)

`config/phases/phase6_evaluation.yaml`'s `sequence`: `score_evaluator` -> `feature_importance_extractor`
-> `error_analyst` -> `hypothesis_generator` -> `experiment_designer`, no critic, `interrupt_after: true`.
All five nodes are real: `score_evaluator` and `feature_importance_extractor` (T-031) are
`ComputeNode`s, and `error_analyst`, `hypothesis_generator` and `experiment_designer` (T-032) are
`LLMNode`s -- the `NoOpNode` fallback is no longer reachable in this phase. The two compute nodes
share experiment-directory resolution and degrade-safe JSON reading via the private `src/nodes/compute/_evaluation_common.py`
module -- ported (not imported, to keep `code_critic`'s Phase 5 module and this Phase 6 module
decoupled) from `code_critic._experiment_dir_from_state`/`_candidate_experiment_dirs`
(`code_critic.py:99-158`): the state-recorded experiment pointer (`state["experiments"][-1]["path"]`)
is tried first, then the well-known `experiments/exp_{iteration}` directory, so both nodes work whether
or not `coder` (T-029, landed) has appended the current iteration's entry to `experiments` yet. The
three LLM nodes deliberately do
**not** share that module -- see the `_evaluation_llm_common.py` paragraph at the end of this section.

- **`score_evaluator`** (`src/nodes/compute/score_evaluator.py`, `ComputeNode` subclass, T-031) -- the
  phase's first node, pure Python, no LLM. Reads the resolved experiment directory's `results.json`
  (`cv_score` key -- mirrors `baseline_runner`'s own results-file shape, pinned as a contract for
  `coder` in `context/discoveries.md`) and `problem_definition.json`'s `success_metric`. It is the
  **sole writer** anywhere in `src/` of `LabState`'s `last_score`, `score_delta`, `best_score`,
  `best_experiment_path` and `iterations_without_improvement`.

  *Metric-direction normalization* (CLAUDE.md invariant #3): `success_metric` is lowercased and every
  non-alphanumeric character stripped, then matched against a curated minimize-oriented set (`rmse`,
  `mse`, `mae`, `mape`, `smape`, `rmsle`, `msle`, `medae`, `logloss`, `crossentropy`, `brier`,
  `hammingloss`, `wmae`); anything else -- including an absent or unrecognized metric -- defaults to
  "maximize". The separator-normalized matching (rather than an exact-string match) is deliberate:
  `"log_loss"`, `"Log-Loss"` and `"Log Loss"` must all resolve identically. A minimize-metric's raw
  score is negated before ever being written to `last_score`/`best_score`, so every score compared
  or stored anywhere in `LabState` is already higher-is-better.

  *Score-delta / tie / first-evaluation rules.* `is_improvement = normalized_score > best_score_before`
  -- **strict** `>`; a tie is never an improvement. `score_delta = normalized_score - best_score_before`
  when `best_score_before` is finite; on the very first evaluation (`best_score` still at its `-inf`
  sentinel from `new_state`), `score_delta` is fixed at `0.0` rather than computed against `-inf` (which
  would otherwise be `+inf`, a non-finite value CLAUDE.md invariant #3 and this artifact's own
  "never write `inf`/`nan`" rule both forbid). `best_score`/`best_experiment_path` are only present in
  the returned delta when `is_improvement` is `True` -- CLAUDE.md invariant #3, enforced here for the
  first time.

  *Liveness.* `iterations_without_improvement` **increments even when no valid score is obtainable**
  (missing/malformed `results.json`, no finite `cv_score`) -- not just on a non-improving evaluated
  score. This is because `src/graph/supervisor.py:31-33` makes `iterations_without_improvement >=
  max_iterations` the *only* exit from the Phase 6 -> Phase 4 iteration loop, and `score_evaluator` is
  the field's sole writer: not incrementing on "nothing to evaluate" would let a permanently-broken
  `results.json` loop the pipeline forever. The "nothing to evaluate" vs. "evaluated and didn't
  improve" distinction that this collapses out of the counter is preserved in the artifact's own
  `evaluated`/`reason` fields instead (`context/decisions.md`, 2026-08-17).

  *Baseline comparison* (`delta_vs_baseline`) is informational only -- it never influences `best_score`,
  `best_experiment_path` or `is_improvement`. It is computed only when `results.json`'s optional
  `metric` field normalizes into `{accuracy, r2, rsquared, score}` (the token set `baseline_runner`'s
  own `.score()` convention, T-020, can produce) AND `state["baseline_score"]` is a finite number;
  otherwise the artifact records `null` plus a reason string. Even when eligible, the subtraction
  itself is re-checked for overflow (see "Never non-finite" below) before being trusted.

  *Output iteration and the experiment-resolution warning.* The artifact's filename number and
  `iteration` field are **not** simply `resolve_iteration`'s entry/`current_iteration` lookup -- they
  come from `_evaluation_common.resolve_output_iteration`, which prefers the *resolved*
  `experiment_dir`'s own trailing `exp_<N>` component, falling back to `resolve_iteration` only when
  the directory doesn't match that shape. Without this, a stale or non-corresponding `iteration` key
  on an `experiments` entry could file a correctly-read report under the wrong number. Separately, when
  an entry declares a valid `iteration` N but its `path` is absent/unusable -- so the well-known
  fallback directory `exp_M` was read instead, with `M != N` -- the artifact's
  `experiment_resolution_warning` field names both. This does not change *which* directory is read
  (that precedence stays a verbatim match with `code_critic`); it makes an adversarial-review-found
  divergence forensically visible instead of a silent false-positive `is_improvement`.

  *Never non-finite.* Writes `reports/score_evaluation_{iteration}.json` unconditionally via
  `workspace.write_json` -- every key always present, `null` (never `inf`/`nan`) for any value that
  would otherwise be non-finite (e.g. `best_score_before` on the first evaluation). This includes the
  *results* of `score_delta`/`delta_vs_baseline`'s subtractions, not just their inputs: two
  individually-finite operands of opposite sign can still overflow to `+-inf` on subtraction, so both
  are re-checked with `math.isfinite` and degraded explicitly (`score_delta` to `0.0`,
  `delta_vs_baseline` to `null`) rather than trusting operand-level finiteness alone.

  *Known limitation (open, not fixed here): polarity is not itself persisted.* `best_score` is stored
  already sign-normalized, with no record of which `direction` produced it. `score_evaluator`
  re-derives direction fresh on every call from `problem_definition.json`, defaulting to "maximize"
  when that file is unreadable -- if it becomes unreadable on a later iteration after being readable
  (with a minimize metric) on an earlier one, a worse raw score can compare as an "improvement" and
  flip `best_score`/`best_experiment_path` to the objectively worse experiment. The fix is a polarity
  field on `LabState`, a protected contract requiring human approval -- out of this task's scope. See
  `context/discoveries.md`'s 2026-08-17 OPEN entry for the concrete repro. Every artifact's
  `success_metric`/`success_metric_raw`/`direction` fields at least make a flip forensically detectable
  after the fact by diffing consecutive reports, even though nothing currently detects it automatically.

- **`feature_importance_extractor`** (`src/nodes/compute/feature_importance_extractor.py`,
  `ComputeNode` subclass, T-031) -- runs second, pure Python, no LLM, **always returns `{}`** (no
  `LabState` field exists for this artifact). It **extracts a pre-computed payload, it does not
  compute anything** -- `design.md`'s "Python + SHAP" phrasing for this node is stale for this
  implementation: there is **no `shap` import anywhere** in the module. `results.json`'s optional
  `feature_importance` (`{feature: value}`) and `feature_names` fields are read, validated, and
  ranked by absolute magnitude into `reports/feature_importance_{iteration}.json`; nothing is fit.
  Gated by an explicit **allow-list** of tree-ensemble `model_family` tokens (`xgboost`, `lightgbm`,
  `catboost`, `extra_trees`, `gradient_boosting_lags`, sourced from `classical_ml_specialist.py:42-47`
  and `timeseries_specialist.py:148-172`) read from the resolved experiment directory's `design.json`
  -- an unrecognized or future model family skips safely with a reason rather than failing or producing
  a meaningless ranking. Skip and success artifacts share one shape (`skipped`, `reason`, `iteration`,
  `experiment_dir`, `experiment_resolution_warning`, `model_family`, `features`,
  `importance_total_overflowed`, `features_truncated`, `original_feature_count`); a success artifact's
  `features` list carries `feature`/`importance`/`normalized_importance`/`rank` per surviving entry.
  Shares the same output-iteration derivation and `experiment_resolution_warning` diagnostic as
  `score_evaluator` above (both delegate to `_evaluation_common.resolve_output_iteration`).

  *Bounded output.* `results.json`'s `feature_importance` payload is LLM/generated-script output, not
  a trusted internal artifact -- an unbounded entry count would otherwise flow uncapped through the
  filtered dict, the ranked list, and the serialized report. Entries beyond `_MAX_RANKED_FEATURES`
  (3000, a generous headroom for real tabular feature engineering) are dropped, keeping the largest by
  absolute magnitude; `features_truncated`/`original_feature_count` record it explicitly when this
  happens, in-band with the report -- the same "never silently drop data without a marker" precedent
  `code_critic._truncate` set for its own text-length artifact caps, adapted to a list. Independently,
  the *sum* of surviving importances (used to compute `normalized_importance`) can itself overflow to a
  non-finite value on just two extreme-magnitude entries; that is guarded and recorded as
  `importance_total_overflowed` -- ranking by raw magnitude stays correct even when it fires, but every
  `normalized_importance` degrades to `0.0` rather than a share of a sum that cannot be represented as a
  finite float.

- **`error_analyst`** (`src/nodes/llm/error_analyst.py`, `LLMNode` subclass, T-032) -- runs third,
  `model_role: reasoning`. Diagnoses the single most likely root cause of the iteration just scored
  and writes `reports/error_diagnosis_{iteration}.json` (`iteration`, `root_cause`, `confidence`,
  `evidence`, `recommended_focus`, `inputs`). `root_cause` is one of five pinned tokens --
  `overfitting`, `underfitting`, `cv_lb_divergence`, `feature_quality`, `wrong_model_family` -- and
  the response is **whitelist-rebuilt**: any other key the model emits is dropped.

  *Inputs, and how the experiment directory is obtained.* Four artifacts:
  `reports/score_evaluation_{iteration}.json` and `reports/feature_importance_{iteration}.json`
  (both written unconditionally by the two compute nodes above, including on their
  "nothing to evaluate"/skip paths), plus the experiment's own `results.json` and `design.json`
  joined onto the **score artifact's own `experiment_dir` field**. The directory is never
  re-derived: `state["experiments"]` is not read (its pointer can name the first rather than the
  last regenerated experiment) and neither `resolve_output_iteration` nor `candidate_experiment_dirs`
  is imported from `src/nodes/compute/` or reimplemented. Score polarity is likewise read
  (`direction`) out of the score artifact rather than re-derived. A directory that is absolute or
  contains a `..` component is rejected without a read attempt.

  *No leaderboard data.* `cv_lb_divergence` is retained as design.md's vocabulary, but no
  leaderboard score is available to this node: `LabState` has no such field, and the
  `kaggle_client` node that fetches one (T-033) runs in **Phase 7, after every Phase 6 node** --
  it records the leaderboard score in `reports/kaggle_submission.json`, never in `LabState`, so
  no leaderboard score exists at the time this node runs. The prompt states this explicitly and
  forbids inventing, assuming or quoting one; the diagnosis inputs are the CV score, the baseline
  score and the feature importance report only. (`config/prompts/error_analyst/v1.md`,
  `error_analyst.py`'s own docstring and `_evaluation_llm_common.ROOT_CAUSES`' comment still
  phrase this as "no leaderboard score anywhere in this pipeline", which was true when they
  landed and remains true *for Phase 6*; the residual imprecision is tracked in
  `context/discoveries/T-033.md`.)

- **`hypothesis_generator`** (`src/nodes/llm/hypothesis_generator.py`, `LLMNode` subclass, T-032) --
  runs fourth, `model_role: reasoning`. Reads the diagnosis, **queries this competition's RAG store
  for what has already been tried**, and writes `reports/hypotheses_{iteration}.json` (`iteration`,
  `hypotheses`, `rag_query`, `prior_attempts_considered`). 1 to 5 hypotheses, each with
  `id`/`statement`/`rationale`/`priority`/`expected_impact`/`addresses_root_cause`; `priority` must
  be a permutation of `1..N` (no gaps, no duplicates) and the list is **stored sorted ascending by
  `priority`**, so "prioritized" is a property of the artifact rather than of the response order.
  `id`s are rejected as duplicates when they match case-insensitively.

  The `RagStore` is an optional keyword-only constructor argument plus a lazy `_ensure_rag_store`
  built from `Settings.load().workspace.chroma_host`/`chroma_port` -- the same convention
  `solution_architect`/`memory_manager`/`web_researcher` use, preserving zero-argument construction
  for `resolve_node`'s `cls()` and giving tests an injection point. The query always names the
  competition and names the diagnosed root cause when one was read; a root-cause token outside the
  pinned vocabulary is never interpolated into the query. `rag_query` and
  `prior_attempts_considered` are stashed on the node during `_build_messages` for `_write_output`
  to inject -- safe because `LLMNode.__call__` runs the two in that order within one call and this
  phase declares `parallel_nodes: []`.

- **`experiment_designer`** (`src/nodes/llm/experiment_designer.py`, `LLMNode` subclass, T-032) --
  runs **last**, `model_role: reasoning`. Converts the hypotheses into an ordered next-iteration
  plan at `reports/experiment_plan_{iteration}.json` (`iteration`, `next_iteration`, `changes`,
  `rationale`). 1 to 6 changes, each with `order`/`change`/`target`/`hypothesis_id`/
  `expected_effect`; `order` must be a permutation of `1..N` and `changes` is **stored sorted
  ascending by `order`**. `target` is one of `solution_plan`, `feature_spec`, `experiment_design`,
  `data`. `hypothesis_id` is deliberately *not* cross-validated against the hypotheses file, which
  may itself have degraded -- and this phase has no critic to retry a rejection.

  *The `current_iteration` increment.* `_build_output_state` returns
  `{"current_iteration": <pre> + 1}`, making this the **only** writer of that field anywhere in
  `src/` -- see the § State mutation rule below. The plan is written to disk and consumed by the
  **next** iteration's Phase 4 (`solution_architect`, `feature_engineer`) by reading the file; it is
  not consumed by the supervisor, which is deterministic Python reading only counters. None of the
  three T-032 nodes adds a `LabState` field (`src/state.py` is a protected contract).

`src/nodes/llm/_evaluation_llm_common.py` is the private module the three LLM nodes share for
fence-stripping/JSON-object extraction, degrade-safe artifact reading and the validators. One
private module rather than three copies, on the same "all consumers land in the same PR" ground
`_evaluation_common.py` carries; it declares no class whose `name` matches its own stem, so
`node_resolver._find_node_class` never mistakes it for a node module, and it is never referenced in
`config/phases/*.yaml`. It deliberately does **not** import
`src/nodes/compute/_evaluation_common.py` (that would contradict T-031's ported-not-imported
decoupling) and does not reimplement `resolve_output_iteration`/`candidate_experiment_dirs` (a fresh
copy could reintroduce the experiment-directory mislabeling bug T-031's adversarial review fixed) --
the resolved directory is read out of the score artifact instead. It is likewise **not** a hoist
into `src/nodes/llm/base.py`: that hoist would mean migrating eight landed node modules and is its
own future task, so this module takes the extraction-copy count from 8 to 9, not to 11. Its readers
catch `DEGRADE_ERRORS = (OSError, ValueError, RecursionError)` and guard `isinstance(path, str)`
from day one, on both the read *and* the re-serialization back into the prompt.

**Known fragility -- the artifact filename number can diverge.** `score_evaluator` and
`feature_importance_extractor` name their reports from `_evaluation_common.resolve_output_iteration`
(derived from the `exp_{N}` directory actually read), while `LLMNode._resolve_output_path` and every
read in the three LLM nodes use `state["current_iteration"]`. Those two can differ when the
state-recorded experiment pointer is stale. That is exactly why every read in this phase's LLM nodes
degrades to an explicit placeholder string instead of raising: a Phase 6 pass whose upstream report
is filed under a different number still produces a diagnosis, hypotheses and a plan rather than
aborting the graph run. `error_diagnosis_{N}.json`'s `inputs` block records which of the four inputs
were actually read, so a degraded diagnosis stays forensically detectable.

### Delivery (Phase 7)

`config/phases/phase7_delivery.yaml`'s `sequence`: `reviewer` -> `report_writer` -> `kaggle_client`,
no critic, `interrupt_after: false`. All three nodes are real (T-033): `reviewer` and
`report_writer` are `LLMNode`s, `kaggle_client` is a `ComputeNode` -- the `NoOpNode` fallback is no
longer reachable in this phase.

**Phase 7 is terminal, and that constrains every node in it.** `src/graph/supervisor.py:31-33`
routes into it only once `iterations_without_improvement >= max_iterations`; nothing runs after it,
there is no critic, no retry, and no later node that could correct or recover anything. So **no node
here may abort the graph**: every read degrades to an explicit placeholder and every failure path
still writes its artifact. `kaggle_client` in particular runs *after* `reports/final_report.md`
already exists -- a crash there would destroy the run's only human-facing deliverable over a
network error.

**Both LLM output patterns are fixed, with no `{iteration}` placeholder** (`reports/code_review.md`,
`reports/final_report.md`): the phase runs exactly once per run and its deliverables belong to the
run, not to an iteration. `LLMNode._resolve_output_path` is therefore not overridden by either node
-- `str.format` harmlessly ignores the unused `iteration` kwarg, the same case `fold_config.json`
and `eda_report.md` already rely on.

**Every Phase 7 read of a Phase 6 artifact uses `current_iteration - 1`.** `experiment_designer` is
the only writer of `current_iteration` anywhere in `src/` and it increments the field **last** in
Phase 6, so Phase 7 always observes `N + 1` while the artifacts on disk are filed under `N`. That
`-1` offset lives in one place, `_delivery_common.previous_iteration`. On a standalone Phase 7 run
(`current_iteration == 0`) it is `-1`, which yields the legal-but-nonexistent relative path
`experiments/exp_-1/...` and therefore a placeholder. It is deliberately **not** clamped to `0`:
clamping would make Phase 7 read `exp_0`'s artifacts on a run that never produced them and report
another experiment's numbers as this run's.

- **`reviewer`** (`src/nodes/llm/reviewer.py`, `LLMNode` subclass, T-033) -- runs first,
  `model_role: implementation`, non-blocking (no critic re-invokes anything on its verdict). Writes
  free-form Markdown to `reports/code_review.md`: `## Verdict` (`clean`/`issues_found`/
  `not_reviewable`), `## Findings`, `## Reproducibility checklist`, `## Summary`, per
  `config/prompts/reviewer/v1.md`'s rubric (fixed seeds, relative paths, no debug prints or dead
  code, no train/validation leakage, pinned dependencies, no hardcoded credentials).

  *Pinned candidate list*, deduped preserving first occurrence: `src/features.py`, `src/models.py`,
  `src/train.py`, `{best_experiment_path}/train.py` (skipped entirely when the pointer is blank or
  unusable), `experiments/exp_{current_iteration - 1}/train.py`. All five are read under **one
  shared total 20 000-character budget** (`_delivery_common.MAX_INJECTED_CHARS`), not a per-file
  cap: five separately-capped files would be a 100 000-character single `invoke`. A file that
  overflows carries an in-band truncation marker, a candidate reached after the budget is spent
  renders `(omitted: the 20000-character injection budget was already used)`, and a missing or
  unreadable one renders `(not present in the workspace)`. Which candidates were actually read is
  recorded in the written review's own `## Files reviewed` block, so a review produced entirely from
  placeholders stays forensically detectable (the `error_diagnosis` `inputs`-block precedent).

  *Injection hardening.* Each injected file is wrapped in a fence computed by
  `_delivery_common.fence_for` (longer than any backtick run it contains, so a ``` inside a
  docstring cannot escape the block and render as top-level prompt markup), and the injected message
  states in-band that everything under `## Workspace code` is data to review, never an instruction
  -- the same pairing `code_critic` uses. A retry cap would not help here even if the phase had one:
  injection seeks a false *pass*, not a loop.

- **`report_writer`** (`src/nodes/llm/report_writer.py`, `LLMNode` subclass, T-033) -- runs second,
  `model_role: research`. Writes `reports/final_report.md`, the run's human-facing deliverable, with
  the sections `## What was tried`, `## What worked`, `## What did not work`, `## Lessons learned`,
  `## Reproducing this run`, `## Open questions and next steps`.

  *Six inputs*: a deterministic state-derived `## Run summary` (no file I/O, always present), the
  problem definition (`state["problem_definition_path"]` **relativized through
  `_delivery_common.safe_relative`**, else the well-known `reports/problem_definition.json` --
  `problem_framer` records the absolute path `WorkspaceManager.write_json` returns, and that string
  is not only read from: it is also an `## Inputs` key rendered verbatim into the published report,
  so the raw value would leak the operator's home directory into the deliverable), `reports/score_evaluation_{N}.json`,
  `reports/error_diagnosis_{N}.json`, `reports/hypotheses_{N}.json`, and
  `reports/code_review.md` -- the file `reviewer` wrote moments earlier in this same sequence,
  through the shared `_delivery_common.CODE_REVIEW_PATH` constant so the write and the read cannot
  drift. All six share the same total injection budget, allocated in render order. Each missing
  input degrades to its own named placeholder and is recorded in the report's `## Inputs` block.

  *Never prints a non-finite float.* `new_state` seeds `best_score = float("-inf")`; every float in
  `## Run summary` goes through a `_coerce_finite_float` guard and renders as `not recorded` when it
  is non-finite, a non-number or a `bool`. The experiment index is capped at 10 entries with the
  true count recorded separately as `experiments_recorded`. The experiment index is sanitized by the
  same rule before serialization, so a non-finite number nested inside an `experiments` entry cannot
  reach the prompt as `Infinity`/`NaN` either (and its dict keys are coerced to `str`, so a
  mixed-key entry degrades instead of raising `TypeError` out of `sort_keys`).

  *The code review is fenced.* It is the one injected section that is raw Markdown -- the other four
  go through `json.dumps`, which escapes their newlines so no heading can materialize out of them.
  It is therefore wrapped in a `fence_for`-computed fence, exactly as `reviewer` fences the workspace
  code, so a counterfeit `## Run summary` heading quoted through the review cannot arrive as a
  second, structurally indistinguishable section.

  *No leaderboard score is available to it.* `kaggle_client` runs **after** this node and files its
  result in `reports/kaggle_submission.json`, never in `LabState`. The prompt states that ordering
  fact explicitly and forbids quoting, assuming or inventing an LB score or rank.

- **`kaggle_client`** (`src/nodes/compute/kaggle_client.py`, `ComputeNode` subclass, T-033) -- runs
  last, pure Python, no LLM (it imports `src/tools/kaggle_client` as a *module*, so nothing
  class-shaped enters its namespace and `_find_node_class` still sees exactly one class; an AST test
  pins the no-`langchain`/no-`src.llm` invariant). Calls the tool's `submit` then `get_score` and
  writes `reports/kaggle_submission.json` with **exactly nine keys, always all present**:
  `competition`, `submission_file`, `submitted`, `lb_score`, `cv_score`, `cv_direction`,
  `divergence`, `divergence_flag`, `reason`. Every `reason` has the absolute workspace root scrubbed
  to `<workspace>` before it is written -- the Kaggle SDK echoes the absolute `file_name` it was
  handed back in its error messages, and this artifact ships inside the published deliverable repo.
  `reason` is `None` only on the fully-successful path;
  otherwise it carries the accumulated reasons joined with `"; "`. `divergence_flag` is always a
  `bool` -- `False` covers both "did not diverge" and "could not be computed", and `reason`
  disambiguates.

  *The submission file must exist before any API call.* This ordering is a contract, not a style
  choice: `kaggle` is installed in this environment and `tests/fixtures/graph_mocks.set_fake_provider_env`
  sets fake `KAGGLE_USERNAME`/`KAGGLE_KEY` for the integration smoke suite, which parametrizes over
  every phase including `phase7_delivery`. Checking the file after `submit()` would make that suite
  issue a live `competition_submit`. The unit test asserting the injected fake API recorded **zero**
  calls, plus the smoke suite's `submitted is False` assertion, are the gates on it.

  *Submission resolution.* First candidate whose absolute path is a file: the experiment directory
  named by `state["best_experiment_path"]`, then `experiments/exp_{current_iteration - 1}`. The
  recorded `submission_file` is always the workspace-relative form, never the absolute path handed
  to the API. **`{best_experiment_path}/submission.csv` is a contract `coder` (T-029) now
  fulfills** -- `coder`'s output contract requires every generated `train.py` to write
  `submission.csv` alongside `results.json` and the OOF predictions in its own experiment
  directory, so once at least one real iteration has run and `score_evaluator` has set
  `best_experiment_path`, this node finds a real file instead of degrading. On a bare/standalone
  workspace (no Phase 5 run behind it — e.g. the integration smoke suite's `phase7_delivery` case)
  `best_experiment_path` is still unset and the "no submission file" degrade path still runs, which
  is what that suite asserts. Same category as T-031's `results.json` `metric` token set; recorded
  in `context/discoveries/T-033.md`.

  *CV de-normalization and the divergence flag.* `score_evaluator` stores every score already
  normalized to higher-is-better, sign-flipping a minimize metric, so `state["best_score"]` for an
  RMSE run is negative. `direction` is read out of `reports/score_evaluation_{current_iteration - 1}.json`
  (`LabState` carries no polarity field), and `cv_raw = best_score` for `maximize`,
  `-best_score` for `minimize`. Then `divergence = cv_raw - lb` for `maximize` and `lb - cv_raw`
  for `minimize`, so a **positive** divergence always means "CV looked better than the leaderboard"
  in both polarities. Only that single score-evaluation candidate is tried -- deliberately
  asymmetric with `reviewer`'s multi-candidate list, because for the divergence *number*, degrading
  to `divergence: null` with a reason beats reporting a silently-wrong figure derived from another
  iteration's polarity. `divergence_flag = abs(divergence) > 0.05`, a **strict** `>` so a divergence
  sitting exactly at the threshold is not flagged.

  **`_DIVERGENCE_THRESHOLD = 0.05` is an absolute difference in the metric's own units and is
  therefore scale-dependent**: an RMSE in the thousands will never flag, and a metric with a tiny
  range will always flag. Accepted for v1 because the flag is advisory and nothing reads it yet; a
  metric-aware or relative threshold (and the extra artifact key it would need) should be revisited
  when the first real consumer lands -- see `context/discoveries/T-033.md`.

  *Nothing reaches `LabState`.* `src/state.py` is a protected contract with no leaderboard field, so
  the LB score, the divergence flag and the submission outcome live in the workspace artifact plus a
  one-line `messages` summary. Any future consumer (an API or frontend surfacing "did we submit,
  what did we score") reads `reports/kaggle_submission.json`. This node is in particular **not** a
  writer of `best_score`/`best_experiment_path` (CLAUDE.md invariant #3).

`src/nodes/llm/_delivery_common.py` is the private module the two Phase 7 LLM nodes share --
`read_workspace_text`, `safe_relative`, `previous_iteration`, `fence_for`, `truncate`,
`read_bounded_texts` (the shared *total* budget), `render_code_sections`, `render_inputs_section`,
`build_markdown_artifact`, plus the `CODE_REVIEW_PATH`/`FINAL_REPORT_PATH` constants. One private
module rather than two copies, on the same "all consumers land in the same PR" ground
`_evaluation_common.py` and `_evaluation_llm_common.py` carry; it declares **no class at all**, so
`node_resolver._find_node_class` can never mistake it for a node module, and it is never referenced
in `config/phases/*.yaml`. Unlike its two predecessors it **imports** `DEGRADE_ERRORS`,
`current_iteration`, `read_workspace_json`, `render_json_section` and the three `*_PATTERN`
constants from `_evaluation_llm_common` (re-exporting them via `__all__`) rather than making a tenth
private copy: none of them carries Phase-6 semantics, and `code_critic` importing from
`_experiment_design` is the standing precedent for a cross-phase private-helper import within
`src/nodes/llm/`. `fence_for` is the one deliberate port, of `data_analyst._fence_for`. There is no
compute-side twin: `kaggle_client` is its only possible consumer and CLAUDE.md invariant #8 forbids
it importing the LLM-side module anyway, so it carries ~25 lines of ported `_relative_to_workspace`/
`_read_json_dict`/`_coerce_*` instead.

**Deliberate asymmetry in experiment-directory resolution**, worth naming because it looks like an
inconsistency: `reviewer` uses the *relativized* `best_experiment_path` directly (it reads via
`WorkspaceManager.read_text`, which wants a relative path), while `kaggle_client` maps it through
`WorkspaceManager.experiment_dir(basename)` (it must hand the Kaggle API an absolute path, and
`experiment_dir` resolves one without creating anything). The two agree for the canonical value
`experiments/exp_N`; they diverge for any pointer that is not of that shape -- a **bare** `exp_3` is
read by `reviewer` as `exp_3/train.py` at the workspace root but resolved by `kaggle_client` to
`experiments/exp_3/submission.csv`, and a nested `foo/bar/exp_3` is likewise relocated by
`kaggle_client` to `experiments/exp_3`.

**Nothing in Phase 7 may abort the graph, including its own writes.** `kaggle_client` catches the
two failures that cannot be recorded in the artifact itself -- the workspace root being unopenable
(`WorkspaceManager.__init__` creates it) and `write_json` failing -- and still returns its
`messages` delta, whose summary line already carries the outcome and gains a marker that the
artifact is missing.

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
| `classical_ml_specialist` | LLM (`LLMNode`) | 5 — Implementation | Landed (T-024) |
| `deep_learning_specialist` | LLM (`LLMNode`) | 5 — Implementation | Landed (T-025) |
| `nlp_specialist` | LLM (`LLMNode`) | 5 — Implementation | Landed (T-026) |
| `timeseries_specialist` | LLM (`LLMNode`) | 5 — Implementation | Landed (T-027) |
| `ensemble_specialist` | LLM (`LLMNode`) | 5 — Implementation | Landed (T-028) |
| `coder` | LLM (`LLMNode`) | 5 — Implementation | Landed (T-029) |
| `code_critic` | LLM (`LLMNode`) | 5 — Implementation | Landed (T-030) |
| `score_evaluator` | Compute (`ComputeNode`) | 6 — Evaluation | Landed (T-031) |
| `feature_importance_extractor` | Compute (`ComputeNode`) | 6 — Evaluation | Landed (T-031) |
| `error_analyst` | LLM (`LLMNode`) | 6 — Evaluation | Landed (T-032) |
| `hypothesis_generator` | LLM (`LLMNode`) | 6 — Evaluation | Landed (T-032) |
| `experiment_designer` | LLM (`LLMNode`) | 6 — Evaluation | Landed (T-032) |
| `reviewer` | LLM (`LLMNode`) | 7 — Delivery | Landed (T-033) |
| `report_writer` | LLM (`LLMNode`) | 7 — Delivery | Landed (T-033) |
| `kaggle_client` | Compute (`ComputeNode`) | 7 — Delivery | Landed (T-033) |

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

**CLAUDE.md invariant #3 — `best_experiment_path`/`best_score` update only on improvement** — enforced
by `score_evaluator` (`src/nodes/compute/score_evaluator.py`, T-031), the sole writer of both fields.
`is_improvement = normalized_score > best_score_before` uses **strict** `>`: a tie against the current
best is never treated as an improvement, and `best_score`/`best_experiment_path` are omitted from the
returned `LabState` delta whenever `is_improvement` is `False`, leaving both fields byte-for-byte
untouched by LangGraph's `LastValue` channel merge. On the very first evaluation of a run,
`best_score` still holds `new_state`'s `-inf` sentinel; `score_delta` is fixed at `0.0` rather than
computed against `-inf` (which is otherwise `+inf`, non-finite and therefore never written anywhere in
`LabState` or an artifact). Every score is normalized to higher-is-better (`score_evaluator`'s own
minimize-metric sign flip, see "Evaluation (Phase 6)" above) before any of this comparison happens —
polarity is resolved once, at the point of comparison, not re-derived per call site.
