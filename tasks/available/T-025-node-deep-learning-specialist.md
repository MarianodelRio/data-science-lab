---
id: T-025
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: available
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [deep_learning_specialist node, experiment design with Optuna search space]
size: S
branch: ~
pr: ~
---

## Node: deep_learning_specialist (Pipeline Phase 5)

**Scope:** `deep_learning_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs neural experiments (TabNet, NODE, MLP with categorical embeddings) with an Optuna search space
- Writes `experiments/exp_{next_id}/design.json`; activated only when the dataset is large enough (guidance in the prompt)
- `model_role: reasoning`

**Done when:**
- [ ] with a mocked LLM the node writes an experiment `design.json` containing a `search_space` and neural `model_family`
- [ ] the design references the frozen folds
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test with mocked LLM, no network
- [ ] `docs/agents.md` row added
