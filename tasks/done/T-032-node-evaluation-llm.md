---
id: T-032
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-008]
status: done
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [error_analyst node, hypothesis_generator node, experiment_designer node]
size: M
branch: feature/T-032-node-evaluation-llm
pr: https://github.com/MarianodelRio/data-science-lab/pull/34
---

## Nodes: error_analyst + hypothesis_generator + experiment_designer (Pipeline Phase 6)

**Scope:** three `LLMNode` subclasses + agent YAMLs + prompts.

**Delivers:**
- `error_analyst`: diagnoses root cause (overfitting/underfitting/CV-LB divergence/feature quality/wrong family); writes `reports/error_diagnosis_{iteration}.json`. `model_role: reasoning`
- `hypothesis_generator`: reads diagnosis + queries RAG to avoid repeating failures; writes prioritized hypotheses. `model_role: reasoning`
- `experiment_designer`: converts hypotheses into a concrete next-iteration plan the supervisor consumes. `model_role: reasoning`

**Done when:**
- [ ] error_analyst (mock LLM) writes a diagnosis JSON with a `root_cause` field
- [ ] hypothesis_generator queries the RagStore (asserted via mock) before producing hypotheses
- [ ] experiment_designer writes a plan with an ordered list of changes
- [ ] all three agent YAMLs + prompts exist and load
- [ ] unit tests with mocked LLM + fake RagStore, no network
- [ ] `docs/agents.md` rows added for all three

## Completed

**What was implemented**

- `src/nodes/llm/_evaluation_llm_common.py` (new, private) — the one shared helper module for the
  three Phase 6 LLM nodes: fence-stripping + brace-salvaging `extract_json_object`, the
  degrade-safe `read_workspace_json`/`render_json_section` pair, `current_iteration` coercion,
  `join_experiment_file`, and the validators (`validate_non_empty_str`, `validate_enum`,
  `validate_int`, `validate_unit_interval`, `validate_str_list`, `validate_object_list`,
  `validate_rank_permutation`). Ships `DEGRADE_ERRORS = (OSError, ValueError, RecursionError)`, the
  `isinstance(path, str)` guard and a `json.dumps` guard from day one. Declares no class whose
  `name` matches its own stem.
- `src/nodes/llm/error_analyst.py` + `config/agents/error_analyst.yaml` +
  `config/prompts/error_analyst/v1.md` — writes `reports/error_diagnosis_{iteration}.json`
  (`iteration`, `root_cause`, `confidence`, `evidence`, `recommended_focus`, `inputs`), whitelist
  rebuilt. Reads four artifacts; `results.json`/`design.json` are joined onto the score artifact's
  own `experiment_dir` field.
- `src/nodes/llm/hypothesis_generator.py` + YAML + prompt — queries the `RagStore` (lazy
  `_ensure_rag_store`, injectable ctor arg) before producing 1–5 hypotheses, stored sorted
  ascending by `priority`, at `reports/hypotheses_{iteration}.json` with `rag_query` and
  `prior_attempts_considered`.
- `src/nodes/llm/experiment_designer.py` + YAML + prompt — writes
  `reports/experiment_plan_{iteration}.json` with `changes` sorted ascending by `order`, and
  **increments `current_iteration` in `_build_output_state`** — the first and only writer of that
  field anywhere in `src/`.
- Tests: `tests/unit/nodes/llm/test_evaluation_llm_common.py` (93),
  `test_error_analyst.py` (38), `test_hypothesis_generator.py` (34),
  `test_experiment_designer.py` (33). LLM mocked at `src.nodes.llm.base.LLMFactory`,
  `WorkspaceManager` patched at both import locations, `RagStore` injected. No network.
- `tests/fixtures/graph_mocks.py` — three new dispatch payloads plus the
  `src.nodes.llm.hypothesis_generator.RagStore` patch.
- `tests/integration/phases/test_phase_subgraphs_smoke.py` — phase6 now asserts the three artifacts
  land on a bare workspace and that `result["current_iteration"] == 1` (the only place the
  increment runs through a real compiled subgraph).
- `tests/unit/graph/test_phase_yaml_contracts.py` — a comment only, naming why the
  `phase6_evaluation` node order is load-bearing (the existing `config.sequence` assertion already
  pins it; no new assertion added, per the Orchestrator's ruling).
- Docs: three rows in `docs/agents.md`; `docs/pipeline.md` § Evaluation rewritten (the `NoOpNode`
  sentence flipped, three per-node bullets, an `_evaluation_llm_common.py` paragraph, the
  filename-number divergence fragility), a `current_iteration` write-ownership bullet in
  § State → State-mutation rules, and three rows in § Node classification.
- `context/decisions/T-032.md` (new, 7 entries), `context/discoveries/T-032.md` (new, 2 open
  entries), plus dated notes appended inside the two existing open `base.py`-hoist discoveries in
  `context/discoveries/legacy.md` (`Status: open` left unchanged on both).

**Deviations from plan**

- **`_resolve_output_path` overridden in all three nodes** (the plan specified no override). The
  plan's own test `test_non_int_current_iteration_coerces_to_zero` expects the artifact to land at
  `reports/experiment_plan_0.json` for a boolean `current_iteration`, which `LLMNode`'s default —
  a raw `state["current_iteration"]` read — does not produce. Without the override the filename
  number and the artifact's own node-injected `iteration` field could disagree
  (`experiment_plan_True.json` containing `"iteration": 0`). `_resolve_output_path` is a documented
  `LLMNode` extension point, so this needed no change to `base.py`.
- **`error_analyst` and `experiment_designer` got an `__init__` override** (the plan said
  `error_analyst` needed none). Both stash values on `self` during `_build_messages` for
  `_write_output` to inject, which is the same hazard the Orchestrator's ruling on Risk 3 addressed
  for `hypothesis_generator` — the attributes are initialized in `__init__` so a direct
  `_write_output` call can never raise `AttributeError`. Zero-argument construction is preserved
  and tested for all three.
- **One validator added beyond the plan's list**: `validate_int` (rejects `bool`), needed by both
  `hypotheses[].priority` and `changes[].order`.
- The task file was still at `tasks/available/` on the branch while `main` had it at
  `tasks/in-progress/`; the branch copy was moved and made byte-identical to main's before this
  section was appended, so the two copies reconcile.

**Key decisions** (full records in `context/decisions/T-032.md`)

- `experiment_designer._build_output_state` is the pipeline's only `current_iteration` writer;
  because `LLMNode.__call__` resolves the output path *before* `_build_output_state`, the node's
  own artifact and all four earlier Phase 6 artifacts stay under the pre-increment number. This
  unblocks the landed `ensemble_specialist`, whose duplicate-`oof_path` invariant raises while
  every base experiment resolves to `exp_0`.
- The resolved experiment directory is read out of `score_evaluation_{N}.json`'s `experiment_dir`,
  never re-derived; `src/nodes/compute/_evaluation_common.py` is neither imported nor
  reimplemented. `state["experiments"]` is deliberately not read (T-030 pointer staleness), and
  score polarity is read (`direction`) rather than re-derived (T-031).
- One private `_evaluation_llm_common.py` instead of three copies, with `base.py` untouched —
  extraction-copy count 8 → 9, not 8 → 11.
- No `LabState` field for any of the three outputs; the supervisor is deterministic Python and
  cannot consume a plan. The absence of any in-code consumer is logged as an open discovery.
- Root-cause enum uses `wrong_model_family` (matching `design.json`'s `model_family` key);
  `cv_lb_divergence` is retained but the prompt states outright that no leaderboard score exists
  anywhere in the pipeline and forbids inventing one.
- `experiment_designer` writes to `reports/experiment_plan_{iteration}.json` rather than
  `design/iteration_{iteration}/…` — it is filed under the pre-increment number and describes the
  iteration just evaluated.

**Dependencies added:** None.
