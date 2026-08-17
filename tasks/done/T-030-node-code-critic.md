---
id: T-030
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: done
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [code_critic node, pass/iterate verdict on generated code, retry guard]
size: S
branch: feature/T-030-node-code-critic
pr: "https://github.com/MarianodelRio/data-science-lab/pull/31"
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
  `max_tokens: 2048`. No `temperature`, so the `implementation` role default applies. Note that
  `max_tokens` is **declarative only**: nothing in `src/` reads `AgentConfig.max_tokens` —
  `LLMFactory` configures the model from `Settings.models.{role}.max_tokens` — so it records intent
  rather than taking effect. That is architecture-wide (every agent YAML carries the field), not a
  T-030 choice; logged as a NOTE in `context/discoveries.md`.
- **`config/prompts/code_critic/v1.md`** — six-part review rubric (reproducibility, relative paths,
  frozen folds as the only CV, per-fold fit scope, inference-pipeline leakage, design faithfulness
  and structure), an explicit "not your job" section, verdict discipline, a machine-parsed output
  contract with a worked example, and an instruction that everything under the generated-code heading
  is data to review rather than an instruction to obey.
- **`tests/unit/nodes/llm/test_code_critic.py`** — 28 test functions / 46 cases, **100% line
  coverage** of the new module, LLM and `WorkspaceManager` mocked, no network and no real filesystem
  writes. `read_text` dispatches per path, so each review section has exactly one possible source and
  section-scoped assertions are real.
- Docs: a `code_critic` bullet in `docs/pipeline.md` § Implementation (Phase 5) plus a
  § Node classification row, and a `docs/agents.md` table row.
- `tests/integration/phases/test_phase_subgraphs_smoke.py` asserts the verdict record lands **and**
  that `final_verdict["forced_pass"] is True` with the full `["iterate", "iterate", "iterate",
  "pass"]` attempt sequence — proving `code_critic` is a live node rather than a `NoOpNode`, and
  pinning the only real-graph coverage of the forced-pass path.

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

### Review corrections (Phase 4, retry 1)

Four reviewers returned 0 blockers and a consolidated fix list; all of it is applied.

**Correctness.** `_parse_verdict` now catches `DEGRADE_ERRORS` rather than a bare `ValueError`,
because `json.loads` raises `RecursionError` — not a `ValueError` subclass — on a deeply nested
payload reachable within this agent's token budget; that was the only path in the node that could
abort the graph run, and it aborted before any record was written. The experiment directory is now
resolved **once** and the context artifacts are pinned to whichever directory yielded `train.py`,
so the critic can no longer review one experiment's code against another's design (a route to a
false *pass*, since the prompt consults `results.json` for early-stopping evidence). `results.json`
and `design.json` are truncated like the code — `results.json` is written by the generated script and
carries the OOF predictions, so an uncapped one measured ~4 000 000 characters in a single `invoke`.
The global-cap `for...else` branch records `code_available: None` and references no loop-local name,
because a negative `max_retries` in a phase YAML really does reach it (`load_phase_config` does not
validate the field) with the loop body never having run. `_extract_verdict_data` adds a
fence-anchored retry: a brace-bearing trailing snippet — the likeliest postamble for a *code* critic —
defeated the shared extractor's brace salvage and destroyed the `feedback`, burning a retry with no
signal. The reviewed code is emitted in a fence longer than any backtick run inside it, and the prompt
states that the code section is data, never instructions.

**Test rigor.** The `WorkspaceManager` mock previously returned one string for every path, so the
Done-when #3 test passed even with the reviewed code removed from the prompt entirely. `read_text`
now dispatches per path and the assertion is scoped to the `## Generated training code` section;
verified by sabotage (stubbing `_read_code` to a constant makes that test fail, and 26 of 44 cases
fail, where previously only 1 did). The same-directory test uses two real candidate directories
instead of one, which is what makes it capable of failing. Added tests pin the `RecursionError`
degrade, both brace-bearing-fence shapes, the fence-escape mitigation, the negative-`max_retries`
path, the `"."`-directory guard, and the record path staying with the original `state`.

**Corrections to earlier claims.** Two statements in the first commit were wrong and are fixed rather
than quietly dropped: `extract_json_object` does not "subsume" `analysis_critic._fence_candidates`
(measured, neither dominates — `analysis_critic` fails both trailing-fence shapes), and the
`graph_mocks` fallback does not "fail JSON parsing" (extraction *succeeds*, salvaging the fold-config
object; it normalizes to `iterate` only because that object has no `verdict` key — hence the
strengthened smoke assertion).

**Logged, not fixed** (`context/discoveries.md`): the retried `coder`'s `experiments` delta is merged
locally but never returned, so `LabState["experiments"]` keeps pointing at the pre-retry experiment
(→ T-029/T-031, measured); `max_retries` is unvalidated in `src/config/loaders.py` (→ infra-agent);
critic feedback reaches `coder` only via the appended verdict message (→ T-029); `AgentConfig
.max_tokens` is inert system-wide; a critic's node-local `messages` concatenation diverges from the
graph's `add_messages` reducer.

### Verification

`pytest --cov=src --cov-fail-under=70 -x` → **1065 passed, 97.15% coverage** (baseline was
1052 passed / 97.05%).
`ruff check .` → all checks passed; `ruff format --check .` → 113 files already formatted;
`mypy src/` → no issues in 64 source files.
`resolve_node("code_critic")` → `src.nodes.llm.code_critic.CodeCriticNode` (not `NoOpNode`).
Unit suite `tests/unit/nodes/llm/test_code_critic.py` → 46 passed, 100% coverage of
`src/nodes/llm/code_critic.py`; phase smoke suite → 9 passed; `test_phase_yaml_contracts.py` +
`test_checkpointer.py` + `test_analysis_critic.py` → 35 passed.
Sabotage check (F6): stubbing `_read_code` to a constant makes
`test_hardcoded_absolute_path_sample_is_flagged` **fail** and 26 of 46 cases fail; before the fixture
rework only 1 case failed under the same stub.
