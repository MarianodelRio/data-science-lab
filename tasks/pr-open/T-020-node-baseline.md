---
id: T-020
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-011, T-006]
status: in-progress
folders: ["src/nodes/llm/", "src/nodes/compute/", "config/agents/", "config/prompts/"]
outputs: [baseline_designer node, baseline_runner node, experiments/baseline/, baseline_score]
size: M
branch: feature/T-020-node-baseline
pr: ~
---

## Nodes: baseline_designer + baseline_runner (Pipeline Phase 3)

**Scope:** `baseline_designer` (`LLMNode`) + `baseline_runner` (`ComputeNode`) + agent YAML + prompt.

**Delivers:**
- `baseline_designer`: reads problem definition + EDA, designs a non-trivial, non-tuned baseline; writes `experiments/baseline/design.json`. `model_role: implementation`
- `baseline_runner`: executes the designed baseline via `code_executor` using frozen folds; writes `experiments/baseline/results.json`; sets `state["baseline_score"]` and `state["baseline_results_path"]`; ensures MLflow tracking URI is configured so the run is logged
- Runs only in Pipeline Phase 3 (supervisor guarantees iteration 0)

**Done when:**
- [ ] baseline_designer (mock LLM) writes `experiments/baseline/design.json`
- [ ] baseline_runner executes via `code_executor` (mocked) and sets a float `state["baseline_score"]`
- [ ] `experiments/baseline/results.json` contains `cv_score`
- [ ] baseline_runner reads folds from `validation/fold_config.json` and does not modify it
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit tests with mocks, no network
- [ ] `docs/agents.md` + `docs/pipeline.md` updated
