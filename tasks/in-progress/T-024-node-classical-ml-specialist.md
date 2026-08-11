---
id: T-024
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: in-progress
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [classical_ml_specialist node, experiment design with Optuna search space]
size: S
branch: feature/T-024-node-classical-ml-specialist
pr: ~
---

## Node: classical_ml_specialist (Pipeline Phase 5)

**Scope:** `classical_ml_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs an experiment for XGBoost/LightGBM/CatBoost/ExtraTrees: model choice, preprocessing, and an Optuna search space
- Writes the design to `experiments/exp_{next_id}/design.json` (design only — the coder implements it)
- `model_role: reasoning`

**Done when:**
- [ ] with a mocked LLM the node writes an experiment `design.json` containing a `search_space` and `model_family`
- [ ] the design references the frozen folds (does not redefine CV)
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test with mocked LLM, no network
- [ ] `docs/agents.md` row added
