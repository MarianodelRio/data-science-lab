---
id: T-021
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-008]
status: done
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [solution_architect node, design/iteration_N/solution_plan.json]
size: S
branch: feature/T-021-node-solution-architect
pr: "https://github.com/MarianodelRio/data-science-lab/pull/23"
---

## Node: solution_architect (Pipeline Phase 4)

**Scope:** `solution_architect` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Reads RAG findings + baseline results + previous error diagnosis; designs the strategy (model families, order, ensembling, realistic ceiling)
- Writes `design/iteration_{current_iteration}/solution_plan.json`; sets `state["solution_plan_path"]`
- `model_role: reasoning`. On high-risk decisions it may consult the `advisor` role.

**Done when:**
- [ ] with a mocked LLM the node writes `design/iteration_0/solution_plan.json`
- [ ] `state["solution_plan_path"]` is set to the iteration-scoped path
- [ ] the output path uses `current_iteration` (test with iteration 0 and 1)
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit test with mocked LLM, no network
- [ ] `docs/agents.md` row added

## Completed

Implemented `SolutionArchitectNode` (`LLMNode`, `model_role: reasoning`), modeled closely on
`baseline_designer.py`'s pattern: overrides `_build_messages` (queries `RagStore` for prior
findings, reads `state["baseline_results_path"]` with graceful not-yet-available/unreadable
degradation), `_write_output` (extracts/validates the LLM's JSON solution plan, writes via
`workspace.write_json`), and `_build_output_state` (returns `{"solution_plan_path": written_path}`
— unlike `baseline_designer`, this node does own a `LabState` path field). No override of
`_resolve_output_path` needed — `LLMNode`'s default `{iteration}` interpolation from
`output_file_pattern` already produces the correct `design/iteration_{N}/solution_plan.json` path.
`config/phases/phase4_design.yaml` already listed `solution_architect` as the first node — no
phase-YAML change needed.

Two scope decisions, both human-approved and logged in `context/decisions.md`:
1. Does not read a "previous error diagnosis" input — `error_analyst` (T-031, Phase 6) doesn't
   exist yet and `LabState` has no field for its output.
2. Does not implement "may consult the advisor role" — no precedent in the codebase for a node
   dynamically invoking a second model role mid-execution, and nothing in this task's acceptance
   criteria requires it.

Review (code-quality, security, adversarial, smoke-test) found three contained validation gaps in
`_validate_solution_plan`, all fixed in a follow-up commit with new test coverage: `model_families`
count wasn't bounded to the prompt's stated 2–4 range; `realistic_ceiling.target_score` accepted
non-finite JSON tokens (`NaN`/`Infinity`/`-Infinity`), which `json.dump`'s default `allow_nan=True`
would have persisted as invalid RFC 8259 JSON and which would silently break any future
`score >= target_score` comparison; and duplicate detection on `model_families`/`order` was
exact-string-match only, so case/whitespace variants of the same model family passed as "distinct."
Security review flagged (INFO only) that `_read_baseline_results` catches `OSError` but
`relative_to_workspace` can raise `ValueError` for an out-of-workspace absolute path — confirmed
as a byte-for-byte pre-existing pattern already in `baseline_designer.py` (T-020), not introduced
here; sandboxing is independently enforced downstream by `WorkspaceManager`. Not fixed as part of
this task.

38 unit tests, all mocked (LLM, `WorkspaceManager`, `RagStore`), no network.
