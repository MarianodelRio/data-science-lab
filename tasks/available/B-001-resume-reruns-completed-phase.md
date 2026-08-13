---
id: B-001
type: bug
agent: TBD
status: available
branch: ~
pr: ~
---

## Resume from checkpoint re-runs the already-completed phase 1

Reported: 2026-08-13
Root cause: INVESTIGATING

`tests/unit/graph/test_checkpointer.py::test_resume_after_restart_does_not_rerun_completed_phase`
fails on clean `main`: `data_analyst` is called 4 times instead of 1. Open in
`context/discoveries.md` since 2026-08-10 (found while verifying T-019).
