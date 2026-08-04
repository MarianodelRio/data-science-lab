---
id: T-017
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-008]
status: blocked
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [literature_researcher node, web_researcher node, RAG indexing with structured metadata]
size: M
branch: ~
pr: ~
---

## Nodes: literature_researcher + web_researcher (Pipeline Phase 2, parallel)

**Scope:** two `LLMNode` subclasses + agent YAMLs + prompts.

**Delivers:**
- `literature_researcher`: queries arxiv + Semantic Scholar; for each source, LLM extracts the `IndexDocument` metadata schema, then indexes into `RagStore`
- `web_researcher`: same pattern via Tavily API
- Both append summaries to `state["research_notes"]`-style pointer or a report file; both use `model_role: research`
- External search clients are injected/mockable

**Done when:**
- [ ] literature_researcher (mock search + mock LLM) indexes ≥1 document into a fake RagStore with populated `problem_type`/`methods_used` metadata
- [ ] web_researcher (mock Tavily + mock LLM) indexes ≥1 document
- [ ] indexed documents conform to the `IndexDocument` schema from T-008
- [ ] both agent YAMLs + prompts exist and load
- [ ] unit tests mock all external calls, no network
- [ ] `docs/agents.md` rows added for both
