---
id: T-023
phase: 2
agent: pipeline-agent
depends_on: [T-011]
status: in-progress
folders: ["src/nodes/compute/", "config/phases/"]
outputs: [specialist_selector compute node]
size: S
branch: feature/T-023-node-specialist-selector
pr: ~
---

## Node: specialist_selector (Pipeline Phase 5, compute)

**Scope:** `src/nodes/compute/specialist_selector.py`. Pure Python, no LLM.

**Delivers:**
- Reads `solution_plan.json`, returns which specialist(s) to activate this iteration, one at a time
- Deterministic mapping from `problem_type` + plan hints to specialist names (`classical_ml`, `deep_learning`, `nlp`, `timeseries`, `ensemble`)
- `ensemble` only eligible once ≥2 specialists have results (checks `state["experiments"]`)

**Done when:**
- [ ] a tabular plan selects `classical_ml_specialist`
- [ ] a plan with text features selects `nlp_specialist`
- [ ] `ensemble_specialist` is not selected until `experiments` has ≥2 entries
- [ ] no LLM import in the module
- [ ] unit tests cover each branch
- [ ] `docs/pipeline.md` "Specialist selection" section updated
