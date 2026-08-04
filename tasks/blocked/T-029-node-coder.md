---
id: T-029
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-006]
status: blocked
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [coder node, workspace training scripts, results.json, OOF predictions, Optuna inner loop]
size: M
branch: ~
pr: ~
---

## Node: coder (Pipeline Phase 5)

**Scope:** `coder` `LLMNode` + agent YAML + prompt. The only node that writes ML implementation code.

**Delivers:**
- Reads specialist design + feature spec; generates training code to `experiments/exp_{id}/train.py` (and updates `src/features.py`/`src/models.py` in the workspace)
- Executes via `code_executor`; on error, reads stderr and iterates (bounded retries)
- Optuna runs inside the subprocess (`n_trials`, early stop from settings); logs to MLflow
- Writes `experiments/exp_{id}/results.json` (cv_score, params, oof path) + artifacts; appends to `state["experiments"]`
- `model_role: implementation`

**Done when:**
- [ ] with a mocked LLM emitting valid code and a stubbed `code_executor`, the node writes `train.py` and `results.json`
- [ ] on a simulated execution error the node re-prompts and retries (bounded), asserted via mock
- [ ] a new entry is appended to `state["experiments"]` with `cv_score` and `path`
- [ ] generated code writes OOF predictions to the artifacts dir (asserted in stubbed result)
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit tests with mocks, no network
- [ ] `docs/agents.md` row added
