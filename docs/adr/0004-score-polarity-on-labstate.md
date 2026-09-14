# ADR 0004 — Score polarity is carried on `LabState`, resolved once, never re-derived

**Date:** 2026-09-14
**Status:** Accepted
**Author:** Architect (dev-team)

## Context

`LabState.best_score` and `last_score` store scores **already normalized to
"higher is better"**: `score_evaluator` (`src/nodes/compute/score_evaluator.py`)
sign-flips minimize-oriented metrics (RMSE, LogLoss, MAE, …) before writing them.
Nothing in the state records *which direction produced a stored score*.

Three sites resolve polarity today, by two different mechanisms:

| Site | How it gets direction |
|---|---|
| `src/nodes/compute/score_evaluator.py` | re-derives it on **every call** from `problem_definition.json`'s `success_metric`; defaults to `"maximize"` when that file is absent or unreadable |
| `src/nodes/compute/kaggle_client.py` (`_cv_from_state`) | reads the `direction` field out of `reports/score_evaluation_{N}.json` |
| `src/nodes/llm/error_analyst.py` | reads `direction` out of the same score artifact |

The re-derivation in the first row is a correctness hazard, raised twice in
`context/discoveries/legacy.md` (T-002 → T-031, and T-031 → the contract owner).
Iteration 0 scores RMSE `2.0` and stores `best_score = -2.0`. If
`problem_definition.json` later becomes unreadable or `problem_definition_path`
is cleared, direction silently falls back to `"maximize"` for that call, and a raw
RMSE of `50.0` is compared as `50.0 > -2.0` — a false improvement that flips
`best_score` and `best_experiment_path` to an objectively worse experiment. This
violates CLAUDE.md invariant #3 silently: both values are floats, so no type or
test catches it, and the two comparisons that spanned different assumed
directions leave no trace in the state.

The artifact-reading sites are safer (they consume what was actually recorded)
but they are each coupled to a specific report filename and iteration number, and
they fail closed with a "no readable score evaluation report" reason whenever
that artifact is missing.

## Decision

**Polarity is a property of the run, carried on `LabState`, established once and
never re-derived per call site.**

1. `LabState` gains a required field
   `score_direction: Literal["minimize", "maximize"] | None`, defaulting to `None`
   in `new_state()` (T-051, human-approved protected-contract change, 2026-09-14).
2. `score_evaluator` is the **sole writer**. It sets `score_direction` on the first
   evaluation that resolves a direction, and on every later call reads the existing
   value and reuses it rather than re-deriving from `problem_definition.json`
   (T-052). A `None` value means "not yet established", not "maximize".
3. Readers use `state.get("score_direction")`, never `state["score_direction"]` —
   runs checkpointed before this field existed rehydrate from SQLite without the
   key, and the LangGraph channel for it is simply empty.
4. No node outside `score_evaluator` derives direction from a metric name. New
   consumers read the state field. `reports/score_evaluation_{N}.json` keeps its
   `direction` field for forensics and for the existing artifact readers until
   they are migrated.

`LabState` cannot *enforce* write-once — it is a plain `TypedDict` and every
non-`messages` field merges last-write-wins through a LangGraph `LastValue`
channel. The invariant is enforced in `score_evaluator`, the same way
`validation_config_path`'s immutability is enforced in `ValidationStrategistNode`
rather than in the state contract.

## Consequences

**Improves.** The silent best-score flip described above becomes impossible once
T-052 lands: a direction established at iteration 0 survives an unreadable
`problem_definition.json` at iteration 5. `kaggle_client`'s CV/leaderboard
de-normalization stops depending on one specific report file being readable.
Polarity becomes inspectable from the run's live state (and from the API
checkpoint read) instead of only from a diff of consecutive report artifacts.

**Costs.** The field is inert between T-051 and T-052 — present, always `None`,
with no writer. During that window there are still three polarity mechanisms in
the tree and the state field is a fourth, unused one; T-052 must close this, and
its scope explicitly covers migrating `kaggle_client._cv_from_state` and
`error_analyst`, or recording why a site stays artifact-based.

**Gets harder.** Runs resumed across the T-051 boundary have no
`score_direction` key in their checkpoint, so every read must tolerate absence —
`.get()` with a `None` branch, permanently, not just during migration. And
`problem_definition.json` changing `success_metric` mid-run no longer changes the
comparison direction: that is the intended behavior (a run's scores must all be
comparable), but it means correcting a genuinely wrong `success_metric` requires
a new run rather than an edit to the file.

**Unchanged.** `best_score = float("-inf")` stays correct as a seed under both
polarities, because `best_score` holds normalized (higher-is-better) values, not
raw metric values. This ADR does not change that and no task should "fix" it.
