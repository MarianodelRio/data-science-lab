---
id: T-051
phase: 6
agent: infra-agent
depends_on: [T-002]
status: done
folders: [src/state.py]
outputs: [LabState.score_direction]
size: S
branch: ~
pr: https://github.com/MarianodelRio/data-science-lab/pull/49
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

## Completed

**What was implemented:**
- Added `score_direction: Literal["minimize", "maximize"] | None` as a
  required key on `LabState` in `src/state.py`, placed in the `# Scores
  (floats only)` block immediately after `score_delta`.
- `new_state()` now sets `score_direction=None` by default, adjacent to the
  other score field defaults. `best_score=float("-inf")` was left untouched.
- Extended the module docstring with a paragraph documenting the contract:
  `None` means "not yet established" (never treated as a synonym for
  "maximize"); the sole future writer is `score_evaluator` (T-052); readers
  must use `state.get("score_direction")`, never subscript access, because
  checkpoints persisted before this field existed and rehydrated later from
  SQLite will permanently lack the key. Also extended the `new_state()`
  docstring with one sentence noting the new default.
- Added three new tests to `tests/unit/test_state.py`:
  `test_new_state_score_direction_defaults_to_none`,
  `test_score_direction_literal_round_trip` (parametrized over
  `["minimize", "maximize"]`), and
  `test_score_direction_missing_key_uses_get_safely` (deletes the key from a
  constructed state and asserts `.get()` still returns `None`, i.e. safe
  under the "permanently missing key" scenario). No existing test body
  needed changes — `test_new_state_has_every_labstate_key` derives its
  expected key set from `LabState.__required_keys__` at runtime and picked
  up the new field automatically.
- Added no runtime enforcement (no validator/setter-guard) — the "set once
  and never silently re-derived" behavior is documented only, per the
  Architect-approved checklist; enforcement is deferred to T-052's
  `score_evaluator`.

**Deviations from plan:**
- The inline comment on the `score_direction` field line in the plan
  (`# None = not yet established; sole writer is score_evaluator (T-052)`)
  pushed the line past `ruff`'s 100-char limit (E501). Moved it to a
  standalone comment line above the field instead, shortened to reference
  the module docstring for the full contract rather than duplicating it
  inline. No functional change.
- `docs/pipeline.md` was **not** touched, per the Architect-approved "Done
  when" checklist in this task's prompt (which supersedes the older
  checklist's line above listing it as delivered) — pipeline-agent owns
  that file and will document `score_direction` in T-052 once
  `score_evaluator` is the field's actual writer.

**Verification:**
- `pytest tests/unit/test_state.py -v` — 21 passed.
- Full suite `pytest --cov=src --cov-fail-under=70 -x` — 2273 passed,
  97.24% total coverage, `src/state.py` at 100% coverage.
- `ruff check . && ruff format --check .` — all checks passed, 167 files
  already formatted.
- `mypy src/` — success, no issues found in 90 source files.
