---
id: T-009
phase: 1
agent: pipeline-agent
depends_on: [T-002, T-003]
status: available
folders: ["src/graph/", "config/phases/"]
outputs: [GraphBuilder, supervisor routing, 7 phase YAMLs, SQLite checkpointer]
size: M
branch: ~
pr: ~
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
- [ ] `GraphBuilder.build()` compiles with placeholder no-op nodes present for missing implementations
- [ ] supervisor routes iteration 0 through phase3 and iteration 1 skips it (unit test on state)
- [ ] supervisor routes to phase7 when `iterations_without_improvement >= max` else phase4
- [ ] checkpointer persists and a run resumes from the last node after simulated restart
- [ ] all 7 phase YAMLs validate against `PhaseConfig`
- [ ] `docs/pipeline.md` "Graph topology" section updated
