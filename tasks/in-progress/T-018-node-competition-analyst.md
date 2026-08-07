---
id: T-018
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-007, T-008]
status: in-progress
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [competition_analyst node, Kaggle forum/notebook findings indexed to RAG]
size: S
branch: feature/T-018-node-competition-analyst
pr: ~
---

## Node: competition_analyst (Pipeline Phase 2)

**Scope:** `competition_analyst` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Uses `kaggle_client` to pull competition forum posts + top-voted notebooks
- LLM extracts winning patterns (features that matter, model families) into `IndexDocument`s, indexes into `RagStore`
- `model_role: research`

**Done when:**
- [ ] with mocked `kaggle_client` + mocked LLM, the node indexes ≥1 structured document into a fake RagStore
- [ ] indexed docs include `methods_used` metadata
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test mocks kaggle + LLM, no network
- [ ] `docs/agents.md` row added
