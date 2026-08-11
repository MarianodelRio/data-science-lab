---
id: T-022
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: done
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [feature_engineer node, design/iteration_N/feature_spec.json]
size: S
branch: feature/T-022-node-feature-engineer
pr: "https://github.com/MarianodelRio/data-science-lab/pull/24"
---

## Node: feature_engineer (Pipeline Phase 4)

**Scope:** `feature_engineer` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Reads solution plan + EDA; designs feature transformations (encoding, null handling, interactions, fold-aware target encoding)
- Writes `design/iteration_{current_iteration}/feature_spec.json`; sets `state["feature_spec_path"]`
- Produces a spec only — writes no implementation code. `model_role: reasoning`

**Done when:**
- [x] with a mocked LLM the node writes `design/iteration_0/feature_spec.json`
- [x] `state["feature_spec_path"]` is set
- [x] the spec explicitly marks target encoding as fold-aware when present (assert key)
- [x] agent YAML + prompt v1 exist and load
- [x] unit test with mocked LLM, no network
- [x] `docs/agents.md` row added

## Completed

Implemented `FeatureEngineerNode` (`src/nodes/llm/feature_engineer.py`), patterned on
`baseline_designer.py`: reads `solution_plan_path`/`eda_report_path` via `WorkspaceManager`,
degrading a missing/unreadable path to a placeholder string (never raising) since T-021
`solution_architect` — the upstream node that populates `solution_plan_path` — had not landed
yet at implementation time. Validates the LLM's JSON response against a strict schema
(`encodings`/`null_handling`/`interactions`), requiring every target-encoding entry to declare
`fold_aware: true` explicitly (rejects both a missing key and an explicit `false`). Overrides
`_build_output_state` to set `state["feature_spec_path"]` — unlike `baseline_designer`, this is
load-bearing for `analysis_critic._detect_phase_stem`'s existing phase-detection logic.

New: `config/agents/feature_engineer.yaml` (`model_role: reasoning`),
`config/prompts/feature_engineer/v1.md`, `tests/unit/nodes/llm/test_feature_engineer.py`
(41 tests), a `docs/agents.md` row. Also updated
`tests/integration/phases/test_phase_subgraphs_smoke.py` with a schema-valid mocked response,
since `feature_engineer` is now a real `LLMNode` instead of a `NoOpNode` placeholder in
`phase4_design`.

Decisions and why (also logged in `context/decisions.md`):
- Target-encoding detection matches a curated set of whole-phrase keywords (`target_encoding`,
  `mean_encoding`, `leave_one_out`, `WOE`, `CatBoost`, `James-Stein`, `M-estimate`,
  `impact_encoding`, ...) via word-boundary regex, not a bare substring check on `"target"`.
  Adversarial review confirmed the original bare-substring approach was both under-inclusive
  (missed the `category_encoders`-family synonyms above, silently letting leaky, non-fold-aware
  encodings through) and over-inclusive (false-triggered on unrelated methods incidentally
  mentioning "target", e.g. `frequency_encoding_excluding_target_leak`). Fixed with regression
  tests covering both directions.
- `fold_aware` must be literal `true`, not merely present as a key — a spec asserting
  `fold_aware: false` on a target-encoding entry is asserting a leakage-prone design and is
  rejected the same as a missing key.
- `_read_eda_report`/`_read_solution_plan` are duplicated per-module (not imported), following
  the established convention (only `relative_to_workspace` itself was hoisted into `base.py`,
  in T-020).

Review: code-quality clean (one minor test-coverage gap, fixed in-PR); security clean; adversarial
found and this PR fixed one confirmed correctness bug (target-encoding detection, above); smoke
tests all pass with direct evidence. Full suite: 563 passed, 96.3% coverage. One pre-existing,
already-documented, unrelated failure (`test_resume_after_restart_does_not_rerun_completed_phase`,
`context/discoveries.md` 2026-08-10 entry) reproduces identically on a clean `origin/main`
checkout and is untouched by this PR.
