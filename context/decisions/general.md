## 2026-08-06 — [Orchestrator, /explore]
Decided: all 3 human checkpoints (`phase1_understanding`, `phase4_design`,
`phase6_evaluation`) are forward-only. No interrupt can trigger re-execution of a phase that
already completed; `human_feedback` is read-only advisory context for future nodes, never a
field that changes graph routing. `supervisor`'s decision at the `phase6_evaluation`
checkpoint stays based exclusively on `iterations_without_improvement` vs `max_iterations` —
it does not read `human_feedback` — making it just as informational as the checkpoints after
phase1/phase4. No `LabState` changes.
Why: keep the model simple for now; this can be revisited later if a real "undo" is needed
(would require a verdict field on `LabState`, a protected-contract change not approved today).
Affects: `T-036` (explainer), `T-041` (chat frontend), `T-034`/`T-037` (resume endpoints), any
future node reading `state["human_feedback"]`. Does not affect `src/graph/` — the code
(`supervisor.py`, `builder.py`) already implements this behavior; this decision only aligns
`design.md`/`docs/pipeline.md`/`IDEA.md`/task-file wording that previously implied otherwise.
Discarded: letting the phase6 checkpoint override `supervisor`'s routing — would require a new
`LabState` field, out of scope now.
