---
id: T-030
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: in-progress
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [code_critic node, pass/iterate verdict on generated code, retry guard]
size: S
branch: feature/T-030-node-code-critic
pr: ~
---

## Node: code_critic (Pipeline Phase 5)

**Scope:** `code_critic` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Reviews generated code for reproducibility (fixed seeds, relative paths), inference-pipeline leakage, and clean structure
- Returns `{verdict: "pass"|"iterate", feedback: str}`; enforces `max_critic_retries` then forces `pass`
- `model_role: implementation`

**Done when:**
- [x] mock LLM returning `iterate` yields non-empty `feedback`
- [x] after `max_critic_retries` iterate cycles the node forces `pass`
- [x] a code sample with a hardcoded absolute path is flagged (prompt-driven; assert via mock verdict)
- [x] agent YAML + prompt v1 exist and load
- [x] unit test with mocked LLM, no network
- [x] `docs/agents.md` row added
- [x] `docs/pipeline.md` § Implementation (Phase 5) subsection added

## Completed

### What was implemented

- **`src/nodes/llm/code_critic.py`** — `CodeCriticNode(LLMNode)`, the last node of Pipeline Phase 5.
  Overrides `__call__` wholesale (T-009's critic pattern) because it owns its own retry control
  flow. Each cycle it injects one `HumanMessage` with four labeled sections — the generated
  `train.py` (fenced, truncated at 20 000 chars with an in-band marker), `design.json`,
  `results.json` and the frozen-fold summary — then parses a `{verdict, feedback}` JSON object,
  re-invoking `coder` on `iterate` and forcing `pass` once the per-target budget is spent.
- **`config/agents/code_critic.yaml`** — `model_role: implementation`, `prompt_version: v1`,
  `tools: []`, `output_file_pattern: reports/code_critic_verdicts_iter{iteration}.json`,
  `max_tokens: 2048`. No `temperature`, so the `implementation` role default applies.
- **`config/prompts/code_critic/v1.md`** — six-part review rubric (reproducibility, relative paths,
  frozen folds as the only CV, per-fold fit scope, inference-pipeline leakage, design faithfulness
  and structure), an explicit "not your job" section, verdict discipline, and a machine-parsed
  output contract with a worked example.
- **`tests/unit/nodes/llm/test_code_critic.py`** — 20 test functions / 33 cases, LLM and
  `WorkspaceManager` mocked, no network and no real filesystem writes. 99% line coverage of the new
  module (the two uncovered lines are the `"."`-directory guard and the provably unreachable
  `for...else` global-cap branch).
- Docs: a `code_critic` bullet in `docs/pipeline.md` § Implementation (Phase 5) plus a
  § Node classification row, and a `docs/agents.md` table row.
- One line added to `tests/integration/phases/test_phase_subgraphs_smoke.py` asserting the verdict
  record lands, which proves `code_critic` is a live node rather than a `NoOpNode`.

### What changed elsewhere

Nothing protected. `config/phases/phase5_implementation.yaml` already registered `code_critic` as
the phase critic (`targets: [coder]`, `max_retries: 3`), so no phase-YAML edit was needed;
`src/state.py`, `src/nodes/llm/base.py`, `src/nodes/llm/_experiment_design.py` (import-only) and
`tests/fixtures/graph_mocks.py` were all left untouched.

### Decisions and why

- **No new `LabState` field.** `src/state.py` is protected and `experiments` still has no writer in
  `src/`, so the script is located at the well-known `experiments/exp_{current_iteration}/train.py`
  (the `design.json` precedent), preferring `state["experiments"][-1]["path"]` when it is usable.
  Because T-029 has not fixed whether that value is the directory or a file inside it, a value
  carrying a suffix is treated as a file pointer and its parent used. The returned delta is
  `{"messages": [...]}` only.
- **Retry budget from the phase YAML**, `load_phase_config("phase5_implementation").critic
  .max_retries`, not `Settings.execution.max_critic_retries` — the phase YAML is the same contract
  that names this node as the critic, so the two cannot drift. The test reads it from the same
  loader rather than hardcoding `3`.
- **`resolve_node` bound through the `node_resolver` module attribute** (not `analysis_critic`'s
  import-time form), which is the B-001 regression guard. Unit tests therefore patch
  `src.graph.node_resolver.resolve_node`, and `src.nodes.llm.code_critic.resolve_node` deliberately
  does not exist.
- **Reused `extract_json_object` / `DEGRADE_ERRORS` / `read_fold_summary`** from
  `_experiment_design.py` instead of adding an eighth private fence-stripper copy.
- **No `try/except` around the target re-invocation** — `coder` has no write-once guard
  (`FoldsAlreadyFrozenError` is `validation_strategist`-specific), so a real crash must surface
  rather than be laundered into a forced pass.
- **`graph_mocks.py` left un-dispatched for `code_critic`**, preserving real forced-pass coverage
  through the integration graph — the same arrangement B-001 chose for `analysis_critic`.

Full rationale in `context/decisions.md` (2026-08-14 — T-030); the `base.py` critic-hoist proposal
and the T-027/T-028 merge-conflict heads-up are in `context/discoveries.md`.

### Verification

`pytest --cov=src --cov-fail-under=70` → **1052 passed, 97.05% coverage**.
`ruff check .` → all checks passed; `ruff format --check .` → 113 files already formatted;
`mypy src/` → no issues in 64 source files.
`resolve_node("code_critic")` → `src.nodes.llm.code_critic.CodeCriticNode` (not `NoOpNode`).
