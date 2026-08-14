---
id: B-001
type: bug
agent: pipeline-agent
depends_on: []
status: done
folders: ["tests/unit/graph/", "tests/integration/phases/", "tests/fixtures/"]
outputs: [tests/unit/graph/test_checkpointer.py, shared network-free graph mock fixture]
size: M
branch: fix/B-001-resume-reruns-completed-phase
pr: "https://github.com/MarianodelRio/data-science-lab/pull/29"
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
- [x] Regression test reproduces the bug: `pytest tests/unit/graph/test_checkpointer.py` alone and
      `pytest tests/unit` produce the same result (no import-order dependence)
- [x] Fix applied
- [x] `test_resume_after_restart_does_not_rerun_completed_phase` passes, asserting phase 1 node
      counts stay at 1 across the resume and phase 2 executed exactly once
- [x] No live network call from any unit test (verified for `competition_analyst`'s
      `list_top_kernels` and every `RagStore`)
- [x] Full suite green: `pytest tests/` with 0 failures
- [x] `ruff check . && ruff format --check .` and `mypy src/` pass
- [x] `context/discoveries.md`'s 2026-08-10 entry marked `resolved in B-001`, correcting the
      "resume re-runs the completed phase" claim
- [x] `docs/pipeline.md` untouched unless the fix changes public behavior (it should not)

## Completed

**Scope held: `src/` unmodified.** The bug's own diagnosis established the checkpointer/resume path
is correct; both defects were in the test. `git diff --stat` contains no `src/` path.

### What was implemented

**`tests/fixtures/graph_mocks.py` (new)** — the shared network-free mock set, extracted verbatim
from `tests/integration/phases/test_phase_subgraphs_smoke.py` (its sole owner until now):
- All eleven `_MOCK_*` payload constants plus `_FAKE_KERNELS`, with their original explanatory
  comments intact.
- `_DISPATCH`, an ordered `(node_name, response)` table replacing the original if-chain. Order is
  load-bearing and preserved exactly — matching is on the `# System prompt — {name}` header line
  because `leakage_auditor`'s own prompt prose mentions "problem_framer".
- `make_llm_side_effect(*, analysis_critic_pass=False)` — the dispatcher factory. The flag is the
  explicit opt-in for a mocked `pass` verdict; `analysis_critic` is deliberately absent from
  `_DISPATCH` so the default keeps the smoke test's pre-existing iterate/forced-pass behavior.
- `graph_llm_mocks(*, analysis_critic_pass=False)` — context manager entering the same ten patches
  with the same ten `return_value` assignments, yielding the mocked LLM. Carries the smoke fixture's
  long explanatory docstring, which is the only record of why each patch exists.
- `set_fake_provider_env(monkeypatch, value)` and `seed_raw_train_csv(workspace_path)` helpers.
- `_MOCK_ANALYSIS_CRITIC_PASS` (new): one payload serves both critic invocations — `data_analyst`
  is in phase 1's `critic.targets`, and in phase 4 `_parse_verdict` normalizes the target to
  `allowed_targets[0]`. The `pass` verdict is honored either way, so no target is re-invoked.

**`tests/fixtures/__init__.py` (new)** — module docstring only; makes `tests.fixtures` a regular
package rather than an implicit namespace package.

**`tests/unit/graph/test_checkpointer.py`** — local constants, `_llm_side_effect` and the
incomplete five-patch stack deleted in favour of `graph_llm_mocks(analysis_critic_pass=True)`.
`_install_counting_resolver` now patches `resolve_node` at **two** locations
(`src.graph.node_resolver` and `src.nodes.llm.analysis_critic`), removing the import-order
dependence. The test seeds `data/raw/train.csv` before the first invoke and asserts the full
phase-1→4 node-count picture plus `next` at both halts.

**`tests/integration/phases/test_phase_subgraphs_smoke.py`** — the moved definitions deleted;
`_mock_llm` shrinks to `set_fake_provider_env(...)` + `with graph_llm_mocks(): yield`, passing no
argument, so its behavior is unchanged. `_seed_phase3_baseline_fixtures` now calls
`seed_raw_train_csv` for the CSV half and keeps its own `fold_config.json` write.

### Decisions and why

1. **`tests/fixtures/` gained a Python module.** design.md sketches that directory as data, but the
   mock set's two consumers live in different trees so neither can own it, and a `tests/`-root
   `conftest.py` would widen the autouse blast radius to ~880 unrelated tests.
2. **The critic opt-in defaults to `False`.** The smoke test's exercise of the iterate → forced-pass
   path (invariant #5) is incidental rather than asserted, but it is real executed coverage;
   defaulting to `pass` would have deleted it silently with every test still green.
3. **`_MOCK_LLM_CONTENT`'s fold split changed to `[0,1,2]`/`[3,4]`.** That constant is dual-purpose
   — generic fallback *and* `validation_strategist`'s fold source, frozen verbatim into
   `validation/fold_config.json` (invariant #1). The checkpointer test's resume reaches phase 3,
   where the real `baseline_runner` trains a real sklearn `LogisticRegression` subprocess against
   those indices; the old single-row, single-class training split crashes there. Verified inert for
   the smoke test (fresh `tmp_path` per case; phase 3 seeds its own folds).
4. **Folds are not pre-seeded in the checkpointer test.** Phase 1 runs for real and freezes them;
   pre-seeding would be overwritten or raise `FoldsAlreadyFrozenError`. Only the dataset is seeded.
5. **`analysis_critic`'s import-time `resolve_node` binding was handled test-side.** The optional
   `src/` change listed in this bug was declined: `src/` needs no fix, and that node's module
   docstring documents the `src.nodes.llm.analysis_critic.resolve_node` patch point as the contract
   its own unit tests depend on.

All five are logged in `context/decisions.md`.

### Verification

| Gate | Result |
|---|---|
| `pytest tests/unit/graph/test_checkpointer.py` (alone) | 2 passed |
| `pytest tests/integration/phases/test_phase_subgraphs_smoke.py` | 9 passed |
| `pytest tests/unit/nodes/llm/test_analysis_critic.py tests/unit/graph/test_checkpointer.py` | 20 passed (was `1 failed, 19 passed` + live 401) |
| `pytest tests/unit` | 883 passed; grep for `kaggle.com`/`HTTPError`/`401` → empty |
| `pytest tests/` | 1019 passed, 0 failed (was `1 failed, 1018 passed`) |
| `ruff check . && ruff format --check .` | clean, 111 files |
| `mypy src/` | Success: no issues found in 63 source files |

### Notes for later

- `context/discoveries.md`: the 2026-08-10 T-019 entry and the 2026-08-13 B-001 entry are both
  marked `RESOLVED`; the T-019 entry keeps its original (wrong) "real bug in the checkpointer"
  claim with an in-place correction appended, so the record shows what was believed and why it was
  wrong.
- A new discovery is addressed to **T-047**: its done-when item at
  `tasks/available/T-047-feature-spec-v2-fit-scope.md:130` points at `_MOCK_FEATURE_SPEC` in the
  smoke test, which now lives in `tests/fixtures/graph_mocks.py` and is seen by both consumers.
- `docs/pipeline.md` untouched — no public behavior changed.

### Review round (2026-08-13)

Verdicts: code-quality CLEAN (3 warnings, 3 nits), security CLEAN (3 INFO), smoke-tester 9/9 PASS,
adversarial WARNING (2 MEDIUM, 1 LOW). No blockers. Nine follow-ups applied in a second commit;
`src/` still untouched, nothing relocated.

- **Third import-time `resolve_node` binder** (MEDIUM). `src/nodes/compute/specialist_selector.py:79`
  binds `resolve_node` at module level and calls it at :233 — the same bug class B-001 fixed for
  `analysis_critic`. No failure today (the run halts at phase 4's interrupt, so phase 5 never
  executes), but the previous comment's claim that patching two locations made counts import-order
  independent was false for the resolver set as a whole. `_install_counting_resolver` now patches all
  three, and the comment enumerates them with a note to extend it if a fourth appears.
- **State-continuity assertions** (MEDIUM). Every assertion was a call count or a `.next` tuple, so
  the test could not distinguish "resume worked" from "resume re-ran the right nodes in the right
  order against a wiped state". Added three assertions that phase 1's `eda_report_path`,
  `problem_definition_path` and `validation_config_path` survived the restart and still point at the
  phase-1 artifacts. **Mutation-verified** — see below.
- **`graph_llm_mocks` construction-time precondition** (INFO). `LLMNode.__init__` calls
  `LLMFactory.get()` at construction (`src/nodes/llm/base.py:78`), so the context manager must wrap
  graph/subgraph *construction*, not only invocation. Documented in the docstring.
- **`LLMFactory._settings` cache reset** (nit). Documented as the caller's responsibility, so the
  asymmetry between the two fixtures (inherited from main, left alone) is explained rather than
  mysterious.
- **`tests/fixtures/__init__.py` docstring** (WARNING) overstated its effect. Softened to match
  `context/decisions.md`: `tests/` has no `__init__.py`, so the import resolves via the root
  `conftest.py` putting rootdir on `sys.path` under pytest's default prepend import mode.
- **`make_llm_side_effect` → `_make_llm_side_effect`** (WARNING). No external consumer; the module
  applies the `_`-prefix convention rigorously elsewhere. Docstring kept intact.
- **`seed_raw_train_csv` docstring** (LOW) named only one of two couplings. The smoke test's phase-3
  case seeds its own literal fold split and never sees `_MOCK_LLM_CONTENT`'s; both copies are now
  named so an editor cannot desynchronize them.
- **`_MOCK_LLM_CONTENT` narrative text** (nit) still self-described as smoke-test-only despite now
  being the checkpointer test's fallback too. Reworded.
- **Three new `context/discoveries.md` entries**: subgraph resume is node-granular inside a phase
  (finer than the code comments claim, and it is what keeps invariant #1 safe); pre-existing live
  `huggingface.co` egress from `tests/tools/test_rag.py` plus the absence of any automated guard for
  the no-network gate; and this file being a "unit" test that runs real subprocesses (candidate
  relocation to `tests/integration/`, deliberately not done here).

**Mutation evidence for the state-continuity fix.** Blanking the three phase-1 path fields via
`second_graph.update_state(...)` between the rebuild and `invoke(None)` left **all 13 count
assertions and the `next` assertion green**, and failed only at the new assertion:

```
>       assert resumed.values["eda_report_path"] == str(workspace_path / "reports" / "eda_report.md")
E       AssertionError: assert '' == '/tmp/pytest-...eda_report.md'
E         - /tmp/pytest-of-mariano/pytest-7/test_resume_after_restart_does0/workspace/reports/eda_report.md
1 failed, 1 passed
```

That is exactly the hole the reviewer described, and it is now closed. Mutation reverted.
