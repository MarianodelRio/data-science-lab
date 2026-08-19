# Retrospective Memory — Architect
<!-- max 25 entries; prune lowest-weight (oldest on tie) when exceeded -->
<!-- Weight: 3 = cross-module/architectural, 2 = design/planning, 1 = implementation detail -->

## L-001 | T-032 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** A state field that many modules read but no module writes is a latent pipeline-wide bug, not a gap to fill later — assign the writer to a named task as soon as the asymmetry is spotted, and check which already-landed modules are silently broken by it.
**Signal:** "Before this, every `{iteration}`-suffixed artifact (`design/iteration_{N}/solution_plan.json`, `feature_spec.json`, `experiments/exp_{N}/design.json`, `reports/score_evaluation_{N}.json`) overwrote its predecessor forever, and the already-landed `ensemble_specialist` could not run at all" *(source: context/decisions)*

## L-002 | T-032 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** "Legal under the module DAG" is not sufficient grounds to allow an import — check whether a prior task deliberately decoupled the two modules, and prefer consuming the sibling's output over importing or copying its logic.
**Signal:** "Importing `src/nodes/compute/_evaluation_common.py` from an LLM node is legal under invariant #8 but contradicts T-031's documented ported-not-imported decoupling, and a fresh copy of `resolve_output_iteration`/`candidate_experiment_dirs` could reintroduce the experiment-directory mislabeling bug T-031's adversarial review fixed." *(source: context/decisions)*

## L-003 | T-032 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** Two modules that name the same artifact family by different rules will diverge silently — when approving a task that reads a sibling's output, verify both sides derive the filename from the same source of truth.
**Signal:** "**The two artifact-numbering schemes inside Phase 6 can disagree.**" *(source: context/discoveries)*

## L-004 | T-032 | 2026-08-19 | Weight: 3
**Folders:** src/nodes/llm/, config/agents/, config/prompts/
**Lesson:** Approving a task that ships producers with no consumer is legitimate incremental delivery, but the missing read side must be logged as an open discovery in the same PR or it will never be wired.
**Signal:** "**The three Phase 6 LLM artifacts have no in-code consumer.**" *(source: context/discoveries)*
