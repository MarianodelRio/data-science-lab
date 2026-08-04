---
id: T-019
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-008]
status: blocked
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [memory_manager node, RAG deduplication + consolidation]
size: S
branch: ~
pr: ~
---

## Node: memory_manager (Pipeline Phase 2)

**Scope:** `memory_manager` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Consolidates the Chroma collection after research: deduplicates near-identical entries, re-scores relevance
- Exposes the RAG query used by later phases ("what did we already try that failed?")
- `model_role: fast`

**Done when:**
- [ ] given a RagStore with two near-duplicate docs, the node reduces them to one (mocked embeddings/similarity)
- [ ] a query returns the consolidated set
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test uses a fake RagStore, no network
- [ ] `docs/agents.md` row added
