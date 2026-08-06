---
id: T-013
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-006]
status: pr-open
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [data_analyst node, eda_report.md, notebooks/01_eda.ipynb]
size: M
branch: feature/T-013-node-data-analyst
pr: https://github.com/MarianodelRio/data-science-lab/pull/15
---

## Node: data_analyst (Pipeline Phase 1)

**Scope:** `src/nodes/llm/data_analyst.py` + `config/agents/data_analyst.yaml` + `config/prompts/data_analyst/v1.md`. Subclasses `LLMNode`.

**Delivers:**
- LLM node that runs EDA by generating Python and executing it via `code_executor` (distributions, correlations, missing, imbalance, cardinality, temporal patterns)
- Writes `reports/eda_report.md` and `notebooks/01_eda.ipynb` via `WorkspaceManager`
- Sets `state["eda_report_path"]`
- `model_role: reasoning`

**Done when:**
- [ ] with a mocked LLM returning fixed EDA code + narrative, the node writes `reports/eda_report.md` and `notebooks/01_eda.ipynb`
- [ ] `state["eda_report_path"]` points to the written report
- [ ] generated code is executed through `code_executor` (asserted via mock), not inline
- [ ] `config/agents/data_analyst.yaml` and `config/prompts/data_analyst/v1.md` exist and load
- [ ] unit test with mocked LLM + mocked code_executor passes, no network
- [ ] `docs/agents.md` row for data_analyst added
