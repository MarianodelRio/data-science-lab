---
id: T-030
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: available
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [code_critic node, pass/iterate verdict on generated code, retry guard]
size: S
branch: ~
pr: ~
---

## Node: code_critic (Pipeline Phase 5)

**Scope:** `code_critic` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Reviews generated code for reproducibility (fixed seeds, relative paths), inference-pipeline leakage, and clean structure
- Returns `{verdict: "pass"|"iterate", feedback: str}`; enforces `max_critic_retries` then forces `pass`
- `model_role: implementation`

**Done when:**
- [ ] mock LLM returning `iterate` yields non-empty `feedback`
- [ ] after `max_critic_retries` iterate cycles the node forces `pass`
- [ ] a code sample with a hardcoded absolute path is flagged (prompt-driven; assert via mock verdict)
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test with mocked LLM, no network
- [ ] `docs/agents.md` row added
