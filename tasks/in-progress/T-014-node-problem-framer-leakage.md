---
id: T-014
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: in-progress
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [problem_framer node, leakage_auditor node, problem_definition.json, leakage_audit.json]
size: S
branch: feature/T-014-node-problem-framer-leakage
pr: ~
---

## Nodes: problem_framer + leakage_auditor (Pipeline Phase 1)

**Scope:** two `LLMNode` subclasses + their agent YAML + prompt files.

**Delivers:**
- `problem_framer`: reads `eda_report.md`, writes `reports/problem_definition.json` with `{problem_type, success_metric, constraints[]}`; sets `state["problem_definition_path"]`. `model_role: fast`
- `leakage_auditor`: reads EDA + problem definition, writes `reports/leakage_audit.json` with `{leaks[], severity, blocks_progression: bool}`. `model_role: reasoning`

**Done when:**
- [ ] problem_framer (mock LLM) writes `problem_definition.json` and sets `problem_definition_path`
- [ ] `problem_definition.json` includes `problem_type` and `success_metric`
- [ ] leakage_auditor (mock LLM) writes `leakage_audit.json` with a boolean `blocks_progression`
- [ ] both agent YAMLs + prompt v1 files exist and load
- [ ] unit tests with mocked LLM, no network
- [ ] `docs/agents.md` rows added for both
