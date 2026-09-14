---
id: T-051
phase: 6
agent: infra-agent
depends_on: [T-002]
status: available
folders: [src/state.py]
outputs: [LabState.score_direction]
size: S
branch: ~
pr: ~
---

## ⚠️ Protected contract — persist score_direction on LabState

**Human-approved protected-contract change.** `LabState` is a protected
contract per `CLAUDE.md`; this change was explicitly approved by the user
during task creation on 2026-09-14.

**Scope:** `src/state.py`. `best_score`/`last_score` comparisons currently
re-derive minimize-vs-maximize direction from `problem_definition.json` on
every call. If that file becomes unreadable on a later iteration after being
readable earlier, direction silently defaults to "maximize," and a worse raw
score can flip `best_experiment_path` to an objectively worse experiment —
violating invariant #3 silently.

**Delivers:**
- New `LabState` field `score_direction: Literal["minimize", "maximize"] |
  None`, set once and never silently re-derived after that
- `new_state()` updated with the correct default

**Done when:**
- [ ] field present with correct typing/default in `LabState` and
      `new_state()`
- [ ] no other protected-contract fields touched
- [ ] existing state tests updated
- [ ] tests written and passing (types per the Testing strategy in `design.md`)
- [ ] `docs/pipeline.md` updated to document the new field's contract
