---
id: T-022
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: blocked
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [feature_engineer node, design/iteration_N/feature_spec.json]
size: S
branch: ~
pr: ~
---

## Node: feature_engineer (Pipeline Phase 4)

**Scope:** `feature_engineer` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Reads solution plan + EDA; designs feature transformations (encoding, null handling, interactions, fold-aware target encoding)
- Writes `design/iteration_{current_iteration}/feature_spec.json`; sets `state["feature_spec_path"]`
- Produces a spec only — writes no implementation code. `model_role: reasoning`

**Done when:**
- [ ] with a mocked LLM the node writes `design/iteration_0/feature_spec.json`
- [ ] `state["feature_spec_path"]` is set
- [ ] the spec explicitly marks target encoding as fold-aware when present (assert key)
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test with mocked LLM, no network
- [ ] `docs/agents.md` row added
