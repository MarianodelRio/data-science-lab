---
id: T-028
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: available
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [ensemble_specialist node, stacking/blending design using OOF predictions]
size: S
branch: ~
pr: ~
---

## Node: ensemble_specialist (Pipeline Phase 5)

**Scope:** `ensemble_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs stacking/blending/weighted-average using out-of-fold predictions from prior experiments (leakage-safe)
- Writes `experiments/exp_{next_id}/design.json` referencing the OOF prediction paths of the experiments it combines
- `model_role: reasoning`

**Done when:**
- [ ] with a mocked LLM and ≥2 prior experiments in state, the node writes an ensemble `design.json` listing the source OOF paths
- [ ] the design uses OOF predictions (not in-fold) — asserted by referenced paths
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test with mocked LLM, no network
- [ ] `docs/agents.md` row added
