---
id: T-031
phase: 2
agent: pipeline-agent
depends_on: [T-011]
status: available
folders: ["src/nodes/compute/", "config/phases/"]
outputs: [score_evaluator node, feature_importance_extractor node, feature_importance_N.json]
size: M
branch: ~
pr: ~
---

## Nodes: score_evaluator + feature_importance_extractor (Pipeline Phase 6, compute)

**Scope:** two `ComputeNode` subclasses. Pure Python, no LLM.

**Delivers:**
- `score_evaluator`: reads latest experiment `results.json`; compares vs `baseline_score` and previous iterations; sets `state["last_score"]`, `state["score_delta"]`, updates `state["best_score"]`/`best_experiment_path` **only if improved**, and increments `iterations_without_improvement` when not improved
- `feature_importance_extractor`: computes SHAP for tree models, writes `reports/feature_importance_{iteration}.json`; **skips silently for neural models** (per design)

**Done when:**
- [ ] score_evaluator sets `score_delta = last_score - best_score_before` correctly (unit test with fixtures)
- [ ] `best_experiment_path` updates only when the new score is better; stays put otherwise
- [ ] `iterations_without_improvement` increments on a non-improving score and resets on improvement
- [ ] feature_importance_extractor writes a JSON for a tree model and returns early (no file) for a neural model
- [ ] no LLM import in either module
- [ ] unit tests cover improve / no-improve / neural-skip
- [ ] `docs/pipeline.md` invariant (best only-improves) noted
