---
id: T-029
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-006, T-047]
status: available
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
- Honors `feature_spec.json` **v2** (T-047): for each `features` entry, `fit_scope: "per_fold"` means the transformation is computed *inside* the CV loop, fitted on the training fold only; `fit_scope: "global"` is applied once outside the loop. No fixed dispatch table — the LLM writes the pandas/sklearn for each `operation` + `params`, using `rationale` as context
- Executes via `code_executor`; on error, reads stderr and iterates (bounded retries)
- Optuna runs inside the subprocess (`n_trials`, early stop from settings); logs to MLflow
- Writes `experiments/exp_{id}/results.json` (cv_score, params, oof path) + artifacts; appends to `state["experiments"]`
- `model_role: implementation`

**Done when:**
- [ ] with a mocked LLM emitting valid code and a stubbed `code_executor`, the node writes `train.py` and `results.json`
- [ ] on a simulated execution error the node re-prompts and retries (bounded), asserted via mock
- [ ] a new entry is appended to `state["experiments"]` with `cv_score` and `path`
- [ ] generated code writes OOF predictions to the artifacts dir (asserted in stubbed result)
- [ ] prompt instructs the v2 `fit_scope` contract: `per_fold` transformations fitted inside the CV loop on the training fold only, `global` ones applied once outside it
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit tests with mocks, no network
- [ ] `docs/agents.md` row added
