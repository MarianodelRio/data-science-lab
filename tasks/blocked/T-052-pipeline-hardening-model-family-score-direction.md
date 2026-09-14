---
id: T-052
phase: 6
agent: pipeline-agent
depends_on: [T-048, T-023, T-051, T-031]
status: blocked
folders: [src/nodes/]
outputs: [specialist_selector fail-fast validation, score_evaluator score_direction consumption]
size: M
branch: ~
pr: ~
---

## Pipeline hardening: fail-fast model_family validation + persisted score direction

**Scope:** `src/nodes/`. Two independent correctness fixes bundled together
as pipeline-agent hardening work.

**Delivers:**
- A supported-`model_family` allowlist in `specialist_selector` (updated to
  reflect T-048's added PyTorch dependency), with an explicit, early,
  clear-error check for any family with no installed runtime — instead of a
  raw `ImportError` surfacing deep inside `coder`-generated code
- `score_evaluator` (and any other reader deriving min/max direction)
  updated to read/write `state["score_direction"]` (from T-051) once instead
  of re-parsing `problem_definition.json` on every call

**Done when:**
- [ ] selecting an unsupported `model_family` raises a clear pipeline error
      before code generation
- [ ] supported families (post-PyTorch) pass through unaffected
- [ ] `score_direction` persists across iterations even if
      `problem_definition.json` later disappears/corrupts (regression test)
- [ ] existing score comparison tests still pass
- [ ] tests written and passing (types per the Testing strategy in `design.md`)
- [ ] `docs/pipeline.md` updated
