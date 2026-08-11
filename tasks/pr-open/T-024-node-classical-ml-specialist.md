---
id: T-024
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: in-progress
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [classical_ml_specialist node, experiment design with Optuna search space]
size: S
branch: feature/T-024-node-classical-ml-specialist
pr: ~
---

## Node: classical_ml_specialist (Pipeline Phase 5)

**Scope:** `classical_ml_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs an experiment for XGBoost/LightGBM/CatBoost/ExtraTrees: model choice, preprocessing, and an Optuna search space
- Writes the design to `experiments/exp_{next_id}/design.json` (design only — the coder implements it)
- `model_role: reasoning`

**Done when:**
- [x] with a mocked LLM the node writes an experiment `design.json` containing a `search_space` and `model_family`
- [x] the design references the frozen folds (does not redefine CV)
- [x] agent YAML + prompt v1 exist and load
- [x] unit test with mocked LLM, no network
- [x] `docs/agents.md` row added

## Completed

Implemented `ClassicalMlSpecialistNode` (`src/nodes/llm/classical_ml_specialist.py`, `LLMNode`)
plus `config/agents/classical_ml_specialist.yaml` (`model_role: reasoning`, `output_file_pattern:
"experiments/exp_{iteration}/design.json"`, `max_tokens: 4096`) and
`config/prompts/classical_ml_specialist/v1.md`.

The bulk of the task is the new shared module `src/nodes/llm/_experiment_design.py` — the
`design.json` contract for all five Phase-5 specialists (T-024–T-028) and their consumer `coder`
(T-029). It exposes `strip_outer_fence`, `extract_json_object`, `normalize_model_family`,
`validate_experiment_design`, `read_fold_summary` and `resolve_feature_spec_ref`, all parameterized
by the calling `specialist` name (the `_research_common.py` convention), and declares no class
matching its own filename stem so `node_resolver._find_node_class` never mistakes it for a node.
`validate_experiment_design` is a whitelist rebuild returning exactly `DESIGN_KEYS` in order;
`specialist`, `feature_spec_ref` and `cv_strategy_ref` are node-injected and never read from the
LLM, and every other unknown top-level key (including `n_trials`/`early_stopping_patience`, which
belong to `config/settings.yaml`'s `optuna:` block) is dropped.

Design decisions worth calling out (10 entries in `context/decisions.md`): experiment id is
`state["current_iteration"]` via the inherited `_resolve_output_path`, with no id allocator, no new
`WorkspaceManager` method and no new `LabState` field; the nine forbidden CV keys are rejected
loudly by exact key name at the top level and inside `search_space`/`fixed_params` (so "does not
redefine CV" is assertable rather than vacuous under a whitelist), with `cv_strategy_ref`
deliberately outside that set; `model_family` is normalized to a canonical token by word-boundary
alias matching and an ambiguous two-family answer is rejected rather than resolved by precedence,
since `coder` dispatches on that value; `search_space` must be non-empty and `log: true` may not be
combined with `step`, both failing at design time instead of inside the Optuna subprocess;
`feature_spec_ref` is relativized (a host-absolute path breaks inside `code_executor`'s subprocess
and inside the container) and stashed on the instance in `_build_messages`, since
`LLMNode.__call__` never passes `state` to `_write_output`.

`read_fold_summary` injects only `strategy`/`n_folds`/`seed` — never `fold_indices` — and every
upstream read degrades to a placeholder rather than raising, so Phase 5 stays invokable standalone.

Two discoveries logged: (1) nothing in `src/` ever increments `current_iteration`, so every
iteration-scoped output path (T-021's, T-022's, `competition_analyst`'s and now this one) resolves
to the same file and silently overwrites — pre-existing, for whoever lands the iteration loop; (2)
an expected `docs/pipeline.md`/context-file conflict with PR #25 (T-023), plus the note that
`docs/agents.md`'s new step-3 exception (the 5 specialists are not listed in
`config/phases/phase5_implementation.yaml`) only becomes true once PR #25's YAML trim lands.

Tests: 125 in `tests/unit/nodes/llm/test_experiment_design.py` (pure functions plus real
`tmp_path`-backed `WorkspaceManager` I/O, no mocks) and 27 in
`tests/unit/nodes/llm/test_classical_ml_specialist.py` (LLM and `WorkspaceManager` mocked, no
network). `tests/integration/phases/test_phase_subgraphs_smoke.py` gained a
`_MOCK_CLASSICAL_ML_DESIGN` payload and one dispatch line. Docs updated: a `docs/agents.md` row
plus the step-3 exception note, and a `### Implementation (Phase 5)` section in `docs/pipeline.md`
with the `design.json` contract block and a node-classification row.

Explicitly not modified: `config/phases/phase5_implementation.yaml`, `src/state.py`,
`src/workspace/workspace_manager.py`, `src/config/*`, `config/settings.yaml`, and every sibling
node module.
