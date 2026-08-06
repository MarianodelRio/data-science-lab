---
id: T-009
phase: 1
agent: pipeline-agent
depends_on: [T-002, T-003]
status: done
folders: ["src/graph/", "config/phases/"]
outputs: [GraphBuilder, supervisor routing, 7 phase YAMLs, SQLite checkpointer]
size: M
branch: feature/T-009-graph-builder
pr: "https://github.com/MarianodelRio/data-science-lab/pull/11"
---

## GraphBuilder + supervisor + phase YAMLs + checkpointer

**Scope:** `src/graph/` + `config/phases/`. **Shared contract** — defines how nodes are discovered and wired.

**Delivers:**
- The 7 `config/phases/*.yaml` files with the **full node list + sequence** per `design.md` (this is the only place phases are composed; node tasks never edit these)
- `GraphBuilder.build() -> CompiledGraph` — for each node name in a phase YAML, dynamically imports `src/nodes/{llm|compute}/{name}.py` and instantiates its node class (convention registry, no central registry file)
- `supervisor(state) -> next_phase` — pure Python conditional edges: Phase 3 only when `current_iteration == 0`; Phase 6 → Phase 4 (continue) or Phase 7 (stop) based on `score_delta`/`iterations_without_improvement`
- SQLite checkpointer at `runs/{run_id}/checkpoint.db`
- Interrupts after Pipeline Phases 1, 4, 6

**Done when:**
- [x] `GraphBuilder.build()` compiles with placeholder no-op nodes present for missing implementations
- [x] supervisor routes iteration 0 through phase3 and iteration 1 skips it (unit test on state)
- [x] supervisor routes to phase7 when `iterations_without_improvement >= max` else phase4
- [x] checkpointer persists and a run resumes from the last node after simulated restart
- [x] all 7 phase YAMLs validate against `PhaseConfig`
- [x] `docs/pipeline.md` "Graph topology" section updated

## Completed

Implemented `GraphBuilder`, `supervisor()`, the 7 `config/phases/*.yaml` files, and a SQLite
checkpointer, per PR #11.

- `src/graph/node_resolver.py` — `resolve_node(name)` dynamically imports
  `src/nodes/{llm|compute}/{name}.py`, falls back to `NoOpNode` when the module doesn't exist yet,
  and (post-review) distinguishes that from a module that exists but has a broken transitive
  import, which now raises `GraphBuilderError` instead of silently no-opping.
- `src/graph/phases/generic.py` — shared sequential/parallel subgraph assembly engine used by all
  7 phase wrappers; validates `parallel_nodes` forms a contiguous block in `sequence` (post-review).
- `src/graph/builder.py` — assembles the top-level `StateGraph[LabState]`, wires fixed + conditional
  edges via `supervisor`, computes `interrupt_after` dynamically from each `PhaseConfig` (with a
  runtime `isinstance(bool)` guard added post-review against quoted-YAML-boolean footguns), and
  attaches the checkpointer.
- `src/graph/checkpointer.py` — `SqliteSaver(sqlite3.connect(...))` at `runs/{run_id}/checkpoint.db`.
- Two design decisions logged in `context/decisions.md` (2026-08-05): critics
  (`analysis_critic`/`code_critic`) and `specialist_selector` own their retry/dispatch control flow
  internally in the future tasks that implement them (T-016/T-023/T-030), rather than via
  graph-level branching — `LabState` has no verdict/retry-count/selected-specialist field, and
  adding one is a separate protected-contract change not approved for this task.
- `pyproject.toml` — added `langgraph-checkpoint-sqlite>=3.1,<4` (previously undeclared;
  `SqliteSaver` needs it).
- Full review round (code-quality, security, adversarial, smoke-tester, mutation-tester) surfaced
  6 findings, all fixed: node-resolver import-error masking, missing fan-in/finish-point structural
  tests, non-contiguous `parallel_nodes` validation, `interrupt_after` type guard, and two docs
  corrections (checkpoint resume granularity is sub-node not phase-node, due to LangChain's ambient
  `RunnableConfig` propagation; `name` must be a plain class attribute, not a Pydantic model field).
  Mutation score on the critical module `src/graph/phases/phase1_understanding.py` (+ its delegate
  `generic.py`): 83.3%, clears the 80% threshold.
- Forward note (not fixed, no caller exists yet): concurrent `GraphBuilder.build()` calls against
  the same `run_id` aren't lock-coordinated across SQLite connections/instances — whichever future
  task adds an API/CLI entry point for run creation should also validate/sanitize `run_id` (path
  traversal risk, noted by the security reviewer) and be aware of this.
