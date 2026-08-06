---
id: T-032
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-008]
status: available
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [error_analyst node, hypothesis_generator node, experiment_designer node]
size: M
branch: ~
pr: ~
---

## Nodes: error_analyst + hypothesis_generator + experiment_designer (Pipeline Phase 6)

**Scope:** three `LLMNode` subclasses + agent YAMLs + prompts.

**Delivers:**
- `error_analyst`: diagnoses root cause (overfitting/underfitting/CV-LB divergence/feature quality/wrong family); writes `reports/error_diagnosis_{iteration}.json`. `model_role: reasoning`
- `hypothesis_generator`: reads diagnosis + queries RAG to avoid repeating failures; writes prioritized hypotheses. `model_role: reasoning`
- `experiment_designer`: converts hypotheses into a concrete next-iteration plan the supervisor consumes. `model_role: reasoning`

**Done when:**
- [ ] error_analyst (mock LLM) writes a diagnosis JSON with a `root_cause` field
- [ ] hypothesis_generator queries the RagStore (asserted via mock) before producing hypotheses
- [ ] experiment_designer writes a plan with an ordered list of changes
- [ ] all three agent YAMLs + prompts exist and load
- [ ] unit tests with mocked LLM + fake RagStore, no network
- [ ] `docs/agents.md` rows added for all three
