---
id: T-028
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: done
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [ensemble_specialist node, stacking/blending design using OOF predictions]
size: S
branch: feature/T-028-node-ensemble-specialist
pr: "https://github.com/MarianodelRio/data-science-lab/pull/32"
---

## Node: ensemble_specialist (Pipeline Phase 5)

**Scope:** `ensemble_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs stacking/blending/weighted-average using out-of-fold predictions from prior experiments (leakage-safe)
- Writes `experiments/exp_{next_id}/design.json` referencing the OOF prediction paths of the experiments it combines
- `model_role: reasoning`

**Done when:**
- [x] with a mocked LLM and ≥2 prior experiments in state, the node writes an ensemble `design.json` listing the source OOF paths
- [x] the design uses OOF predictions (not in-fold) — asserted by referenced paths
- [x] agent YAML + prompt v1 exist and load
- [x] unit test with mocked LLM, no network
- [x] `docs/agents.md` row added

## Completed

`ensemble_specialist` is the fifth and last Pipeline Phase 5 specialist. It reads the
solution plan, the frozen fold summary and the out-of-fold (OOF) predictions of prior
experiments, and designs one stacking / blending / weighted-average combiner into
`experiments/exp_{iteration}/design.json`.

**Implemented (inside `folders:`)**

- `src/nodes/llm/ensemble_specialist.py` — `EnsembleSpecialistNode(LLMNode)`, mirroring
  `timeseries_specialist`'s `_build_messages` / `_write_output` override pair and its
  instance-stash mechanism, plus the `base_experiments` machinery:
  `_build_base_experiments`, `_oof_path_for_experiment`, `_experiment_dir_from_entry`,
  `_experiment_id`, `_fallback_iteration`, `_render_base_experiments`.
- `config/agents/ensemble_specialist.yaml` — `model_role: reasoning`,
  `output_file_pattern: experiments/exp_{iteration}/design.json`.
- `config/prompts/ensemble_specialist/v1.md` — prompt v1.
- `src/nodes/llm/_experiment_design.py` — **additive only**: `ENSEMBLE_DESIGN_KEYS`,
  `_validate_base_experiments`, `validate_ensemble_design`. `DESIGN_KEYS` and
  `validate_experiment_design` are byte-for-byte unchanged.

**Decisions and why**

- **`output_file_pattern` uses `{iteration}`, not the task's `exp_{next_id}`** — no id
  allocator exists in the system; T-027 set this precedent.
- **Wrapper over parameter widening.** `validate_ensemble_design` calls
  `validate_experiment_design` unchanged and appends the ninth key, rather than adding a
  keyword-only parameter the four landed siblings would have to keep *not* passing. Keeps
  the frozen `assert tuple(result) == DESIGN_KEYS` test passing untouched.
- **`base_experiments` is node-injected from `state["experiments"]`, never read from the
  LLM response** — same convention as `feature_spec_ref` / `cv_strategy_ref`. All entries,
  in order, never filtered by the LLM. This is what makes the "lists the source OOF paths"
  criterion deterministically assertable.
- **OOF discovery**: each experiment's own `results.json` → `oof_path` when present and it
  re-relativizes cleanly, else the `oof_predictions.parquet` convention in that
  experiment's directory. Binding on `coder` (T-029) — recorded in `context/discoveries.md`.
- **Schema floor is non-empty, not `>= 2`.** The `>= 2` eligibility rule belongs to
  `specialist_selector._should_ensemble`'s routing, not to this schema to re-derive.
- **Alias table deliberately omits bare `weighted` / `weight` / `stack` / `stacked`.**
  `normalize_model_family` has no longest-match-wins rule (T-026 discovery), so only the
  qualified multi-word forms are aliased — this resolves `"weighted blend of stacked
  models"` to `blending` alone. It structurally *cannot* defuse `"blended stacking"`
  (both words are canonical self-match aliases); that is handled at the prompt level with
  explicit rejected examples, mirroring `timeseries_specialist`'s own hybrid case.
- **No `config/phases/*.yaml` registration.** `specialist_selector` already hardcodes
  `"ensemble_specialist"` and dispatches via `resolve_node`; adding it to the phase node
  list would execute it a second time as an unconditional edge.
- **Runtime-unreachable today**, and the module docstring and docs say so plainly: nothing
  writes `state["experiments"]` yet (`coder`/T-029, its only producer, is blocked), so
  `specialist_selector` cannot satisfy its own `>= 2` check in a real run. Same posture as
  the four landed siblings, whose consumer also does not exist yet.

**Also changed** (outside the node's own files, each for a stated reason — the T-024/T-027
precedent; `folders:` deliberately not widened)

- `tests/unit/nodes/llm/test_ensemble_specialist.py` — new; the task's own unit tests.
  `tests/` is outside `folders:` for every node task by construction.
- `tests/unit/nodes/llm/test_experiment_design.py` — tests for the three additions, plus a
  `DESIGN_KEYS` length/order regression tripwire guarding the backward-compat requirement.
- `tests/unit/nodes/compute/test_specialist_selector.py` — **exactly one** added test,
  `test_real_resolve_node_resolves_landed_ensemble_specialist`. The NoOp re-pointing chain
  was terminated by T-027's OPEN discovery and was deliberately not touched.
- `docs/agents.md` — the row required by the Done-when checklist.
- `docs/pipeline.md` — the Phase 5 node bullet (before `code_critic`), the node
  classification table row, the `design.json` contract section (now noting the ninth key),
  and the stale `specialist_selector` paragraph claiming `ensemble_specialist` still falls
  back to `NoOpNode`.
- `context/decisions.md` — the four non-obvious decisions above, plus the 2026-08-18
  review-fix entry below.
- `context/discoveries.md` — two entries: the sharper `current_iteration` self-overwrite
  hazard, and the OOF-path convention binding on T-029.

**Verification**

`pytest` 1328 passed, global coverage 95%; `ruff check` + `ruff format --check` clean;
`mypy src/` clean. `tests/tools/test_rag.py` (13 tests) fails identically on clean `main` —
`sentence_transformers` is not installed in this environment — and is unrelated to T-028.

## Completed — review fix (2026-08-18)

Adversarial review found a real correctness BLOCKER plus two lower-severity gaps. All three
fixed in this pass.

**BLOCKER — fallback-numbering collision produced a duplicate `oof_path`.** `_experiment_id`
numbered its fallback by the entry's raw list **index** (`experiment_{index}`) while
`_fallback_iteration` (used for the fallback directory) numbered by the entry's own recorded
**`iteration`**, falling back to the index only when `iteration` was absent or the wrong
type — two independent numbering sources for the same degraded entry. Reproducer:
`_build_base_experiments({"experiments": [{"iteration": 1}, {}]}, ws)` (both entries missing
`path`) produced two distinct ids (`experiment_0`/`experiment_1`) both pointing at
`experiments/exp_1/oof_predictions.parquet`. `_validate_base_experiments` did not check for
duplicates, so this would have written verbatim into `design.json`, and `coder` (T-029) would
fit a meta-learner reading one experiment's OOF predictions twice under two labels while
silently dropping the real second source — no error anywhere in the pipeline.

Fixed both halves:
- **Unified the numbering (`src/nodes/llm/ensemble_specialist.py`).** `_experiment_id`'s
  fallback now derives from `_fallback_iteration(entry, index)` too — the same value
  `_experiment_dir_from_entry` uses — instead of the raw list index. Chosen over the
  alternative (drop the `iteration` preference, number both purely by position) because it
  preserves `_fallback_iteration`'s original correctness property (the fallback directory
  stays pointed at the entry's own well-known location even when entries are read out of
  order or interleaved with `path`-less entries) and extends that same property to the id,
  rather than discarding it for a smaller code change. Both `_fallback_iteration`'s and
  `_experiment_id`'s docstrings were rewritten to state the unified rule — the old
  `_fallback_iteration` docstring described itself as only building "the fallback experiment
  directory," which no longer held once `_experiment_id` started reading it too.
- **Added a duplicate-`oof_path` rejection to `_validate_base_experiments`
  (`src/nodes/llm/_experiment_design.py`).** Unifying the numbering does not make a
  collision impossible by itself (two entries can still carry genuinely coinciding
  `iteration` values), so `_validate_base_experiments` now tracks `oof_path`s seen so far and
  raises `ValueError` — in the function's existing "internal error" phrasing style, naming
  both colliding `experiment_id`s and the shared `oof_path` — the moment a second entry
  resolves to a path already claimed. With the reproducer above, both entries now resolve to
  the *same* id (`experiment_1`) and the *same* `oof_path`, and this raises rather than
  writing. That is correct, not a regression: two `state["experiments"]` entries resolving
  to the same OOF source is a genuinely unrepresentable ensemble design, and failing loudly
  at design-write time beats silently double-counting one source. Two entries with real,
  distinct fallback numbers still produce distinct `oof_path`s and write successfully.

**Test coverage (adversarial Finding 2).** No existing test paired two co-occurring degraded
entries. Added: the exact reproducer now raising with nothing written
(`test_two_degraded_entries_with_colliding_fallback_numbers_raise_and_write_nothing`);
`_validate_base_experiments` rejecting a duplicate `oof_path` directly, unit-level
(`test_validate_base_experiments_rejects_duplicate_oof_path_directly`) and accepting distinct
ones (`test_validate_base_experiments_accepts_distinct_oof_paths_directly`); two legitimately
distinct degraded entries still writing successfully
(`test_two_legitimately_distinct_degraded_entries_write_successfully`); and the unified
numbering rule itself, pinning that a degraded entry's fallback id and its resolved directory
agree (`test_degraded_entry_fallback_id_and_directory_agree`) — plus a matching
`validate_ensemble_design`-level duplicate test
(`test_duplicate_oof_path_across_entries_raises`) in `test_experiment_design.py`.

**Prompt rejected-examples widened (adversarial Finding 3, low severity).** Added five
plausible LLM phrasings the adversarial reviewer found ambiguous but previously unwarned
against — `"super learner blend"`, `"weighted average of blends"`, `"convex combination
blend"`, `"holdout blend with learned weights"`, `"a stacked super learner with weighted
blend"` — to `config/prompts/ensemble_specialist/v1.md`'s `## model_family` rejected-examples
list, and extended `test_ambiguous_multiword_phrasings_raise`'s parametrization to cover them.
`_MODEL_FAMILIES` and the no-retry behavior were left unchanged, as directed — both remain
accepted, documented design decisions.

**Verification (this pass):** `pytest --cov=src --ignore=tests/tools/test_rag.py -q` — 1339
passed, coverage 95%; `ruff check .` + `ruff format --check .` clean; `mypy src/` clean.
`tests/tools/test_rag.py` still fails identically on clean `main` (`sentence_transformers` not
installed) — unrelated, excluded as before.
