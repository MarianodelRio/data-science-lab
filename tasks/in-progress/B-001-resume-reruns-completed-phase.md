---
id: B-001
type: bug
agent: pipeline-agent
depends_on: []
status: in-progress
folders: ["tests/unit/graph/", "tests/integration/phases/", "tests/fixtures/"]
outputs: [tests/unit/graph/test_checkpointer.py, shared network-free graph mock fixture]
size: M
branch: fix/B-001-resume-reruns-completed-phase
pr: ~
---

## `test_resume_after_restart_does_not_rerun_completed_phase` fails on clean `main`

Reported: 2026-08-13 (open in `context/discoveries.md` since 2026-08-10, found while verifying T-019)

`main` is red: the full unit suite on a clean checkout gives `1 failed, 882 passed`, and this
test is the only failure.

### The reported premise is wrong — the resume path is NOT broken

The discovery entry claims resuming from a checkpoint re-runs the completed phase 1. Verified
false. Driving `GraphBuilder().build()` directly with `analysis_critic` mocked to a `pass`
verdict:

```
AFTER FIRST INVOKE: {data_analyst: 1, problem_framer: 1, validation_strategist: 1,
                     leakage_auditor: 1, analysis_critic: 1}
next = ('phase2_research',)          <- interrupt_after landed correctly
AFTER RESUME:       {...phase1 all still 1..., literature_researcher: 1, web_researcher: 1,
                     competition_analyst: 1, memory_manager: 1, baseline_designer: 1}
next = ('phase3_baseline',)
```

Phase 1 is not re-executed on resume. The checkpointer, `interrupt_after` and the SQLite thread
wiring all work. Nothing in `src/graph/` needs fixing — `src/` is out of scope for this task.

### Root cause — two independent defects, both in the test

**1. The mock LLM has no `analysis_critic` branch.** `_llm_side_effect`
(`tests/unit/graph/test_checkpointer.py:61-77`) falls back to `_MOCK_LLM_CONTENT` for the critic.
`_parse_verdict` (`src/nodes/llm/analysis_critic.py:143`) normalizes that to `iterate`, so the
critic genuinely re-invokes `data_analyst` `max_retries: 3` times at
`src/nodes/llm/analysis_critic.py:313` before forcing a pass. **1 graph call + 3 critic retries
= the observed 4.** That is correct product behavior (CLAUDE.md invariant #5) — the assertion is
measuring critic retries, not resume.

**2. The failure is import-order dependent, and the other branch makes a live network call.**
`analysis_critic` binds `resolve_node` at import time (`src/nodes/llm/analysis_critic.py:31`,
`from src.graph.node_resolver import resolve_node`), unlike `src/graph/builder.py:70-72` which
deliberately goes through the module attribute so tests can monkeypatch it. Result:

| Run | Critic retries counted? | Failure |
|---|---|---|
| `tests/unit/graph/test_checkpointer.py` alone | yes (module not yet imported) | `assert 4 == 1` |
| Full suite / after `tests/unit/nodes/llm/test_analysis_critic.py` | no (real `resolve_node` already bound) | live Kaggle `401` |

Both confirmed by running the pair explicitly. In the second path the first assert passes, the
test reaches phase 2, and `competition_analyst`'s default `list_top_kernels` hits
`https://www.kaggle.com/api/v1/kernels/list` for real — a direct violation of the CLAUDE.md
quality gate "No network calls in unit tests".

**3. Latent, hits whoever fixes the above.** `phase2_research` and `phase3_baseline` are
`interrupt_after: false`, so `second_graph.invoke(None)` does not stop after phase 2 — it runs
through to the phase 4 interrupt. The test therefore also needs phase 3 / phase 4 mocks
(`baseline_designer`, `baseline_runner`/mlflow, `solution_architect`, `feature_engineer`, the
specialists). `tests/integration/phases/test_phase_subgraphs_smoke.py:216-295` already has that
entire fixture.

**Location:** `tests/unit/graph/test_checkpointer.py:126-157` (the test), root causes at
`tests/unit/graph/test_checkpointer.py:61-77` (missing critic branch) and
`tests/unit/graph/test_checkpointer.py:79-105` (incomplete patch set).

### Fix

- Add an `analysis_critic` dispatch branch to the mocked LLM returning a `pass` verdict
  (`{"verdict": "pass", "feedback": ..., "target_node": "data_analyst"}`), so phase 1 node counts
  measure graph-driven execution rather than critic retries.
- Extract the network-free mock set from
  `tests/integration/phases/test_phase_subgraphs_smoke.py` into a shared helper (e.g.
  `tests/fixtures/graph_mocks.py`) and use it from both tests, rather than duplicating it. The
  smoke test currently exercises the critic's iterate/forced-pass path on purpose, so the shared
  dispatcher needs an explicit opt-in flag for the critic verdict — do not silently change the
  smoke test's behavior.
- Cover the phase 3 / phase 4 nodes the resume actually reaches, so `invoke(None)` completes at
  the phase 4 interrupt instead of crashing.
- Make the test independent of import order: patch `resolve_node` at **both**
  `src.graph.node_resolver` and `src.nodes.llm.analysis_critic`, or assert only on graph-driven
  calls.
- Optional, implementer's call (not required by this bug): switch `analysis_critic` to
  `node_resolver.resolve_node(...)` for consistency with `builder.py`'s stated convention. This
  removes the order dependence at the source but breaks the
  `src.nodes.llm.analysis_critic.resolve_node` patch points in that node's own tests, and touches
  `src/` — if taken, it needs its own justification in `context/decisions.md`.

**Done when:**
- [ ] Regression test reproduces the bug: `pytest tests/unit/graph/test_checkpointer.py` alone and
      `pytest tests/unit` produce the same result (no import-order dependence)
- [ ] Fix applied
- [ ] `test_resume_after_restart_does_not_rerun_completed_phase` passes, asserting phase 1 node
      counts stay at 1 across the resume and phase 2 executed exactly once
- [ ] No live network call from any unit test (verified for `competition_analyst`'s
      `list_top_kernels` and every `RagStore`)
- [ ] Full suite green: `pytest tests/` with 0 failures
- [ ] `ruff check . && ruff format --check .` and `mypy src/` pass
- [ ] `context/discoveries.md`'s 2026-08-10 entry marked `resolved in B-001`, correcting the
      "resume re-runs the completed phase" claim
- [ ] `docs/pipeline.md` untouched unless the fix changes public behavior (it should not)
