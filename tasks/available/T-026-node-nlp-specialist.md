---
id: T-026
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: available
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [nlp_specialist node, experiment design with Optuna search space]
size: S
branch: ~
pr: ~
---

## Node: nlp_specialist (Pipeline Phase 5)

**Scope:** `nlp_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs text experiments: TF-IDF baselines, sentence-transformer embeddings, optional fine-tuning; with an Optuna search space
- Writes `experiments/exp_{next_id}/design.json`; activated only when text features exist
- `model_role: reasoning`

**Done when:**
- [ ] with a mocked LLM the node writes an experiment `design.json` containing a `search_space` and a text-based `model_family`
- [ ] the design references the frozen folds
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test with mocked LLM, no network
- [ ] `docs/agents.md` row added
