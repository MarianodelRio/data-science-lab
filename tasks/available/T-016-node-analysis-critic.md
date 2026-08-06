---
id: T-016
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: available
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [analysis_critic node, pass/iterate verdict with feedback, retry guard]
size: S
branch: ~
pr: ~
---

## Node: analysis_critic (Pipeline Phases 1 & 4)

**Scope:** `analysis_critic` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Reviews analytical outputs (EDA, problem framing, solution plan, feature spec) against a methodological-rigor rubric
- Returns a structured verdict `{verdict: "pass"|"iterate", feedback: str, target_node: str}`
- Enforces `max_critic_retries` (from settings, default 3): after N iterate cycles on the same target, forces `pass` and records that the retry budget was exhausted
- `model_role: fast`

**Done when:**
- [ ] mock LLM returning `iterate` yields a verdict with non-empty `feedback` and a `target_node`
- [ ] after `max_critic_retries` iterate verdicts on one target, the node forces `pass` (test drives the counter)
- [ ] `verdict` is always one of `pass`/`iterate`
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit tests with mocked LLM, no network
- [ ] `docs/agents.md` row added
