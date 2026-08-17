---
id: T-027
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: pr-open
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [timeseries_specialist node, experiment design with Optuna search space]
size: S
branch: feature/T-027-node-timeseries-specialist
pr: "https://github.com/MarianodelRio/data-science-lab/pull/30"
---

## Node: timeseries_specialist (Pipeline Phase 5)

**Scope:** `timeseries_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs temporal experiments: lag features, rolling statistics, ARIMA/Prophet univariate baselines; with an Optuna search space
- Writes `experiments/exp_{next_id}/design.json`; activated only when temporal structure exists; must respect temporal CV (no future leakage)
- `model_role: reasoning`

**Done when:**
- [ ] with a mocked LLM the node writes an experiment `design.json` containing a `search_space` and temporal features
- [ ] the design references the frozen (time-aware) folds and never uses future data
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test with mocked LLM, no network
- [ ] `docs/agents.md` row added
