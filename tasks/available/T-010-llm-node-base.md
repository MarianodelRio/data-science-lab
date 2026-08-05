---
id: T-010
phase: 1
agent: pipeline-agent
depends_on: [T-002, T-004, T-005]
status: available
folders: ["src/nodes/"]
outputs: [LLMNode base class, context trimming, standard node lifecycle]
size: M
branch: ~
pr: ~
---

## LLM node base + convention registry (src/nodes/llm/base.py)

**Scope:** `src/nodes/` base classes only. **Shared contract** — every LLM node subclasses this.

**Delivers:**
- `LLMNode` base class: constructor loads its `AgentConfig` (by class-declared `name`), gets its model via `LLMFactory.get(config.model_role)`, loads its prompt via `PromptLoader`
- `__call__(state: LabState) -> dict` lifecycle: trim context (`last_n_messages` per `settings.context`), build messages, invoke LLM, write output via `WorkspaceManager`, return the state delta (path + any scalars)
- Context trimming helper honoring `max_messages_per_node`
- Convention: a node lives at `src/nodes/llm/{name}.py` exposing a class whose `name` matches the config filename (so GraphBuilder can import by name — no central registry)

**Done when:**
- [ ] a sample subclass declaring `name="dummy"` loads its config + prompt and, with a mocked LLM, returns a state delta
- [ ] context trimming keeps only the last N messages
- [ ] the node writes its output through `WorkspaceManager` (asserted via mock), never directly
- [ ] LLM is mocked — no network calls
- [ ] `mypy src/nodes/llm/base.py` passes
- [ ] `docs/agents.md` "Adding an agent" section updated
