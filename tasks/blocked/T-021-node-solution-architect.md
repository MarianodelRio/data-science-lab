---
id: T-021
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-008]
status: blocked
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [solution_architect node, design/iteration_N/solution_plan.json]
size: S
branch: ~
pr: ~
---

## Node: solution_architect (Pipeline Phase 4)

**Scope:** `solution_architect` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Reads RAG findings + baseline results + previous error diagnosis; designs the strategy (model families, order, ensembling, realistic ceiling)
- Writes `design/iteration_{current_iteration}/solution_plan.json`; sets `state["solution_plan_path"]`
- `model_role: reasoning`. On high-risk decisions it may consult the `advisor` role.

**Done when:**
- [ ] with a mocked LLM the node writes `design/iteration_0/solution_plan.json`
- [ ] `state["solution_plan_path"]` is set to the iteration-scoped path
- [ ] the output path uses `current_iteration` (test with iteration 0 and 1)
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test with mocked LLM, no network
- [ ] `docs/agents.md` row added
