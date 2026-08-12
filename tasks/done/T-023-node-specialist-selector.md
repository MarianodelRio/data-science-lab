---
id: T-023
phase: 2
agent: pipeline-agent
depends_on: [T-011]
status: done
folders: ["src/nodes/compute/", "config/phases/"]
outputs: [specialist_selector compute node]
size: S
branch: feature/T-023-node-specialist-selector
pr: "https://github.com/MarianodelRio/data-science-lab/pull/25"
---

## Node: specialist_selector (Pipeline Phase 5, compute)

**Scope:** `src/nodes/compute/specialist_selector.py`. Pure Python, no LLM.

**Delivers:**
- Reads `solution_plan.json`, returns which specialist(s) to activate this iteration, one at a time
- Deterministic mapping from `problem_type` + plan hints to specialist names (`classical_ml`, `deep_learning`, `nlp`, `timeseries`, `ensemble`)
- `ensemble` only eligible once ≥2 specialists have results (checks `state["experiments"]`)

**Done when:**
- [x] a tabular plan selects `classical_ml_specialist`
- [x] a plan with text features selects `nlp_specialist`
- [x] `ensemble_specialist` is not selected until `experiments` has ≥2 entries
- [x] no LLM import in the module
- [x] unit tests cover each branch
- [x] `docs/pipeline.md` "Specialist selection" section updated

## Completed

Implemented `SpecialistSelectorNode` (`ComputeNode`, `src/nodes/compute/specialist_selector.py`).
Reads both `state["problem_definition_path"]` (`problem_type`) and `state["solution_plan_path"]`
(`model_families`/`order`/`ensembling_strategy`/`rationale`) — scope adjustment from the task's
literal wording, human-approved during Phase 1 analysis, since `solution_plan.json` alone has no
`problem_type` field. Builds one normalized text blob and selects a specialist via fixed
keyword precedence (timeseries → nlp → deep_learning → classical_ml default), mirroring
`feature_engineer`'s curated-keyword-family convention. Ensemble override requires BOTH
`len(state["experiments"]) >= 2` AND a non-empty `ensembling_strategy` that doesn't say "no
ensembling" — the experiment-count check short-circuits first, so `ensemble_specialist` can
never be selected with fewer than 2 experiments regardless of the strategy text. Dispatches via
`resolve_node(chosen)(state)` (mirrors `analysis_critic`'s internal re-invocation pattern, T-009)
and returns the specialist's delta verbatim; falls back to `NoOpNode` today since T-024–T-028
don't exist yet.

Also fixed a pre-existing bug in `config/phases/phase5_implementation.yaml` (human-approved,
protected-contract change): trimmed `nodes`/`sequence` to `[specialist_selector, coder,
code_critic]`, removing the 5 specialist names, which were wired as real graph edges
(`generic.py` chains `sequence` pairwise) and would have caused the graph to invoke every
specialist unconditionally once T-024–T-028 land, on top of `specialist_selector`'s own internal
one-specialist dispatch. Updated `tests/unit/graph/test_phase_yaml_contracts.py` to match.

Two decisions logged in `context/decisions.md`: the timeseries/nlp-before-deep-learning keyword
precedence order, and the phase5 YAML trim rationale. One discovery logged in
`context/discoveries.md`: the keyword-matching design has no negation/context awareness (e.g. "no
BERT needed" still selects `nlp_specialist`) — accepted as a v1 limitation, flagged for whoever
builds T-025/T-026/T-027 next.

Full review round (code-quality, security, adversarial, smoke-tester; mutation-tester skipped —
module not in `devteam.config.yml`'s `critical_modules`, `require_mutation_tests: false`) found
zero blockers. One coverage nit (absolute-path branch untested despite being the real production
path) fixed with 2 added tests; adversarial review independently executed and confirmed the
absolute-path handling was already correct even before the added tests.

32 unit tests, all real `WorkspaceManager`/`tmp_path` fixtures plus one un-mocked-`resolve_node`
test confirming the genuine `NoOpNode` fallback path. Full suite: 498 passed, 1 pre-existing
unrelated failure (`test_checkpointer.py`, already tracked as an open discovery from T-019).
