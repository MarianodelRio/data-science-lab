---
id: T-015
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: blocked
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [validation_strategist node, validation/fold_config.json (immutable)]
size: M
branch: ~
pr: ~
---

## Node: validation_strategist (Pipeline Phase 1) ⚠️ freezes folds

**Scope:** `validation_strategist` `LLMNode` + agent YAML + prompt. **Enforces the immutable-folds invariant.**

**Delivers:**
- Reads problem definition + EDA; selects CV strategy (stratified/group/time-series/adversarial)
- Generates concrete fold indices and writes `validation/fold_config.json` with `{strategy, n_folds, fold_indices, seed}`
- Sets `state["validation_config_path"]`
- **Write-once guard:** if `fold_config.json` already exists, the node must NOT overwrite it — it raises `FoldsAlreadyFrozenError`
- `model_role: fast`

**Done when:**
- [ ] with a mocked LLM the node writes `validation/fold_config.json` containing `strategy` and `fold_indices`
- [ ] `state["validation_config_path"]` is set
- [ ] calling the node a second time when the file exists raises `FoldsAlreadyFrozenError` and leaves the file byte-identical
- [ ] fold_indices cover all rows exactly once (partition check in test)
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit tests with mocked LLM, no network
- [ ] `docs/agents.md` + `docs/pipeline.md` invariant note updated
