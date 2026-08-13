---
id: T-025
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: done
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [deep_learning_specialist node, experiment design with Optuna search space]
size: S
branch: feature/T-025-node-deep-learning-specialist
pr: "https://github.com/MarianodelRio/data-science-lab/pull/28"
---

## Node: deep_learning_specialist (Pipeline Phase 5)

**Scope:** `deep_learning_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs neural experiments (TabNet, NODE, MLP with categorical embeddings) with an Optuna search space
- Writes `experiments/exp_{next_id}/design.json`; activated only when the dataset is large enough (guidance in the prompt)
- `model_role: reasoning`

**Done when:**
- [x] with a mocked LLM the node writes an experiment `design.json` containing a `search_space` and neural `model_family`
- [x] the design references the frozen folds
- [x] agent YAML + prompt v1 exist and load
- [x] unit test with mocked LLM, no network
- [x] `docs/agents.md` row added

## Completed

`deep_learning_specialist` is an `LLMNode` mirroring `classical_ml_specialist` (T-024) structurally:
the same three injected prompt sections (`## Solution plan`, `## Frozen CV folds`, `## Feature spec
reference`), the same instance-stash of the feature-spec reference between `_build_messages` and
`_write_output` (`LLMNode.__call__` never passes `state` to the latter), the same shared validator,
the same output path, and no `_build_output_state` override.

**`src/nodes/llm/_experiment_design.py` was not modified.** That was the central constraint: it is
the contract T-026–T-028 inherit and T-029 consumes. It did not need modifying because
`normalize_model_family(value, allowed, specialist)` takes the family table as a *parameter* — so
this node declares its own node-local `_MODEL_FAMILIES` (`tabnet`, `node`, `mlp`) and passes it
through `validate_experiment_design`. `extract_json_object`, `read_fold_summary`,
`resolve_feature_spec_ref` and the `DEGRADE_ERRORS` tuple are all imported, not re-implemented, so
this PR adds no eighth JSON-extraction copy.

`node` was kept as the canonical token for Neural Oblivious Decision Ensembles despite colliding
lexically with the pipeline's own "node" vocabulary: whole-phrase word-boundary matching makes it
safe (`nodes`, `NODEv2`, `node_count` do not match), and its spelled-out aliases sit in the same
family, so `"NODE (Neural Oblivious Decision Ensembles)"` resolves to one family while a genuinely
two-family answer (`"MLP node"`) is still rejected as ambiguous.

**Two neural-specific rules live in the prompt, not the validator**, because neither is expressible
in the current schema, and both are called out as unenforced in `docs/pipeline.md`:
1. *Fit scope.* `preprocessing` is a flat token list with no fit-scope notion, and
   `FORBIDDEN_CV_KEYS` matches dict keys rather than list values — so a scaler fitted before the CV
   split is silent feature-statistic leakage across the frozen folds that no guard catches. The
   prompt requires fitting inside each fold and recommends encoding the scope in the token itself
   (`standard_scaler_fitted_per_fold`). Handed to T-029/T-031 as an OPEN discovery, cross-referenced
   to T-047's `fit_scope` work.
2. *Scalar-only architecture parameters.* `_validate_choices`/`_is_json_scalar` reject a list-valued
   `choices`, so tuning over layer-width tuples is impossible; the prompt decomposes the architecture
   into `n_layers`/`layer_width`/`width_decay`/`embedding_dim_multiplier`. The asymmetry is stated
   explicitly, since `fixed_params` *does* accept a flat scalar list (one fixed `hidden_dims` is
   legal) — and both directions are pinned by tests.

The task's "activated only when the dataset is large enough" condition is prompt-level, and could not
be otherwise: `LabState` has no row-count or shape field, and `specialist_selector` matches keywords
against a text blob with no size input. Since selection has already happened when this node runs and
no other specialist is queued behind it, the prompt forbids refusing — it degrades capacity (a modest
MLP rather than TabNet) and records the concern in `rationale`.

**Test-safety fix.** `tests/unit/nodes/compute/test_specialist_selector.py`'s two "unlanded
specialist" tests were pointed at `deep_learning_specialist` (commit `ebcb795`) precisely because it
was the next unlanded one. Once this module exists, `resolve_node` discovers it by convention and
`test_real_resolve_node_falls_back_to_noop_and_returns_empty_dict` — which seeds a `pytorch cnn` plan
and runs the *real* selector — would dispatch into a real `LLMNode` and attempt a live API call from
a unit test. Both tests were re-pointed to `nlp_specialist` (the NLP branch precedes the
deep-learning branch in `_select_by_signal`, so a text signal is a stable route) and a landed-case
test was added for this node.

**Output path decision** (human-confirmed at the Phase-1 checkpoint, closing T-024's OPEN discovery):
all five specialists keep `experiments/exp_{iteration}/design.json`. Invariant #7 guarantees one
specialist per iteration and `specialist_selector.run` dispatches exactly once; a `{specialist}`
component would force `coder` to glob a directory or gain a new `LabState` field (protected contract)
and would require editing a landed agent's YAML from a task scoped to a different node. The
constraint is now written down in `docs/pipeline.md`, with the escape hatch in `context/decisions.md`.

Tests: 53 new in `tests/unit/nodes/llm/test_deep_learning_specialist.py` (LLM and `WorkspaceManager`
mocked, no network), 34 passing in the re-pointed selector module, and a new
`test_phase5_subgraph_routes_neural_plan_to_deep_learning_specialist` integration test that seeds a
neural plan and asserts keyword branch → `resolve_node` → real node → file on disk (replacing the
neural-routing coverage the re-pointing removed). Every model-family case parametrizes over the
**imported production table**, never a copy — the mutation-survival defect T-024's review round
found. Two extra tests pin the prompt to the validator: one asserts the prompt's worked example
equals this module's payload constant, the other feeds that example through the real validator.

Suite-wide: 943 passed, coverage 96.92%. `ruff check`, `ruff format --check` and `mypy src/` all
clean. One unrelated test fails — `tests/unit/graph/test_checkpointer.py::test_resume_after_restart_does_not_rerun_completed_phase`
— which is a **pre-existing failure on `origin/main`**, verified by running it on a clean main
checkout; it has been open in `context/discoveries.md` since T-019 and is outside this task's scope
(`src/graph/`).

Explicitly not modified: `src/nodes/llm/_experiment_design.py`, `src/nodes/llm/base.py`,
`src/nodes/llm/classical_ml_specialist.py` and its YAML/prompt, `config/phases/phase5_implementation.yaml`
(the specialists must stay absent or `generic.py` would execute this node a second time as a real
graph edge), `src/nodes/compute/specialist_selector.py`, `src/state.py`, `src/graph/`, `pyproject.toml`.

Three OPEN discoveries logged: the fit-scope gap above; **PyTorch is not a dependency**
(`pyproject.toml` has no `torch`/`pytorch-tabnet`, so a `coder`-generated neural script will
`ImportError` — infra-agent's call, and it affects image size and CI time); and a **collision warning
for T-026**, which was claimed while this task was being implemented and will conflict in the selector
test plus both docs files — whoever merges second must re-point the NoOp tests again, to
`timeseries_specialist`.
