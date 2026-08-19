---
id: T-033
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-007]
status: in-progress
folders: ["src/nodes/llm/", "src/nodes/compute/", "config/agents/", "config/prompts/"]
outputs: [reviewer node, report_writer node, kaggle_client node, final_report.md]
size: M
branch: feature/T-033-node-delivery
pr: ~
---

## Nodes: reviewer + report_writer + kaggle_client (Pipeline Phase 7)

**Scope:** `reviewer` + `report_writer` (`LLMNode`) + `kaggle_client` node (`ComputeNode`) + agent YAMLs + prompts.

**Delivers:**
- `reviewer`: reviews the final workspace code (fixed seeds, relative paths, no debug prints); writes a review summary. `model_role: implementation`
- `report_writer`: generates `reports/final_report.md` (what was tried, what worked, lessons). `model_role: research`
- `kaggle_client` node: uses the `kaggle_client` tool to format + submit the best submission, retrieves LB score, flags CV/LB divergence in state

**Done when:**
- [ ] reviewer (mock LLM) writes a review summary file
- [ ] report_writer (mock LLM) writes `reports/final_report.md`
- [ ] kaggle_client node calls the tool's `submit` + `get_score` (mocked) and records LB score + divergence flag in state
- [ ] agent YAMLs + prompts exist and load
- [ ] unit tests with mocks, no network
- [ ] `docs/agents.md` rows added
