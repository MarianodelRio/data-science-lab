---
id: T-002
phase: 0
agent: infra-agent
depends_on: [T-001]
status: pr-open
folders: ["src/"]
outputs: [LabState TypedDict, state helper functions]
size: S
branch: feature/T-002-labstate-contract
pr: "https://github.com/MarianodelRio/data-science-lab/pull/5"
---

## LabState contract (src/state.py)

**Scope:** `src/state.py` only. This is a **shared contract** — every node reads/writes it.

**Delivers:**
- `LabState(TypedDict)` exactly as defined in `design.md` § Shared contracts (all fields, correct types, `messages: Annotated[list, add_messages]`)
- `new_state(competition_name, workspace_path) -> LabState` factory with sensible defaults (`current_iteration=0`, `iterations_without_improvement=0`, `best_score=-inf`, empty paths/lists)
- Type-only module: no I/O, no LLM, no side effects

**Done when:**
- [x] `LabState` contains every field listed in `design.md` with matching types
- [x] `new_state("x", "/tmp/x")` returns a dict where `current_iteration == 0` and `experiments == []`
- [x] `mypy src/state.py` passes with no errors
- [x] unit test asserts every required key is present after `new_state()`
- [x] `docs/pipeline.md` "State" section updated

## Completed

Implemented `LabState(TypedDict)` and `new_state()` in `src/state.py`, copied verbatim from
`design.md` § Shared contracts (21 fields, `messages: Annotated[list, add_messages]`).
`new_state()` returns via TypedDict call syntax (mypy catches missing/misspelled fields for
free, no `cast`/`# type: ignore` needed). Two factory defaults not specified by design.md's
LabState block itself were inferred and logged in `context/decisions.md`: `max_iterations=10`
(sourced from `config/settings.yaml`'s `execution.max_iterations`) and `phase=""` (avoids
coupling this protected contract to pipeline-agent's phase-naming convention).

11 unit test functions (17 parametrized items) in `tests/unit/test_state.py`, 100% coverage on
`src/state.py` (critical module, 85% bar). Mutation testing scored 100% (5/5 killed). Security
and code-quality reviews: clean/approved. Smoke tests: 5/5 PASS.

Adversarial review (triggered by unanimous approval from the other four reviewers) found four
forward-looking gaps in the contract's design — not bugs in this diff, which matches design.md
verbatim as required:
- No documented score-polarity convention (`best_score`/`last_score` assume "higher is better";
  minimize metrics like RMSE need normalization by whoever writes them)
- Only `messages` has a LangGraph reducer; Phase 2's concurrent `literature_researcher ‖
  web_researcher` step could hit `InvalidUpdateError` if a future node pair writes the same key
- `phase`/`score_delta` write-ownership and formula were undocumented
- `experiments: list[dict]` has no nested type (key-name drift risk across future writers)

Per explicit human decision, these were logged as OPEN entries in `context/discoveries.md`
(targeted at T-009 GraphBuilder and T-031 score_evaluator) rather than blocking T-002, plus the
`phase`/`score_delta` ownership gap was addressed directly via a `docs/pipeline.md` clarification
in the same PR. No `LabState` field/type changes — the contract still matches `design.md`
verbatim.
