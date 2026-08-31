---
id: T-029
phase: 2
agent: pipeline-agent
depends_on: [T-010, T-006, T-047]
status: pr-open
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [coder node, workspace training scripts, results.json, OOF predictions, Optuna inner loop]
size: M
branch: feature/T-029-node-coder
pr: https://github.com/MarianodelRio/data-science-lab/pull/37
---

## Node: coder (Pipeline Phase 5)

**Scope:** `coder` `LLMNode` + agent YAML + prompt. The only node that writes ML implementation code.

**Delivers:**
- Reads specialist design + feature spec; generates training code to `experiments/exp_{id}/train.py` (and updates `src/features.py`/`src/models.py` in the workspace)
- Honors `feature_spec.json` **v2** (T-047): for each `features` entry, `fit_scope: "per_fold"` means the transformation is computed *inside* the CV loop, fitted on the training fold only; `fit_scope: "global"` is applied once outside the loop. No fixed dispatch table — the LLM writes the pandas/sklearn for each `operation` + `params`, using `rationale` as context
- Executes via `code_executor`; on error, reads stderr and iterates (bounded retries)
- Optuna runs inside the subprocess (`n_trials`, early stop from settings); logs to MLflow
- Writes `experiments/exp_{id}/results.json` (cv_score, params, oof path) + artifacts; appends to `state["experiments"]`
- `model_role: implementation`

**Done when:**
- [ ] with a mocked LLM emitting valid code and a stubbed `code_executor`, the node writes `train.py` and `results.json`
- [ ] on a simulated execution error the node re-prompts and retries (bounded), asserted via mock
- [ ] a new entry is appended to `state["experiments"]` with `cv_score` and `path`
- [ ] generated code writes OOF predictions to the artifacts dir (asserted in stubbed result)
- [ ] prompt instructs the v2 `fit_scope` contract: `per_fold` transformations fitted inside the CV loop on the training fold only, `global` ones applied once outside it
- [ ] agent YAML + prompt v1 exist and load
- [ ] unit tests with mocks, no network
- [ ] `docs/agents.md` row added

## Completed

- Implemented `CoderNode` (`src/nodes/llm/coder.py`), `config/agents/coder.yaml`, and
  `config/prompts/coder/v1.md` per the Orchestrator/Planner's expanded scope (the full
  Done-when checklist passed to the Coder superseded the shorter list above — file-existence
  checklist boxes were not ticked in place because the file's original checklist predates that
  expansion; every item in the expanded list is satisfied, see below):
  - `CoderNode.__call__` overrides `LLMNode.__call__` wholesale (precedent: `code_critic`),
    running an execute-then-re-prompt loop bounded by `_MAX_EXECUTION_RETRIES = 2` (3 attempts
    total). Each attempt: extract the single fenced ```python block (`_extract_code`), write it
    to `experiments/exp_{iteration}/train.py`, execute for real via `code_executor.execute`,
    and validate the run (`_validate_run` — timeout, nonzero exit, `results.json`
    readability/`cv_score`/`metric`, `submission.csv` presence, OOF artifact presence, in that
    order). On failure short of the last attempt, the failure reason + stderr are appended as a
    new `HumanMessage` and the loop re-prompts; on exhaustion it raises `ValueError` (no
    forced-pass at this layer — that is `code_critic`'s separate loop).
  - Reads `design.json` (`_read_design`) and `feature_spec.json` (`_read_feature_spec`, via the
    shared `resolve_feature_spec_ref`) and the frozen fold summary (shared `read_fold_summary`),
    all degrading to placeholders on `_experiment_design.DEGRADE_ERRORS`, never raising before
    the first LLM call.
  - Appends exactly one entry (`id`, `path` = the experiment *directory*, `cv_score`,
    `iteration`, `model` from `design.json["model_family"]`) to the **whole** `state["experiments"]`
    list (read-copy-append-return, since the field has no LangGraph reducer).
  - `config/prompts/coder/v1.md` documents: the four input sections, the exact three-artifact
    output contract, the `fit_scope` v2 dispatch-free contract, column safety
    (`repr()`/`json.dumps()`, an explicit "never string-concatenate" instruction, worked
    example), the `design.json`/model-family dispatch contract, ARIMA `order`/`seasonal_order`
    defensive parsing, Optuna/MLflow literal-value injection, the frozen-folds-only-CV rule, the
    `FORBIDDEN_CV_KEYS` gaps (case-sensitivity + the named holdout/fold-shaping keys, with
    `forecast_horizon` always honored), `gradient_boosting_lags` → `lightgbm` default,
    unavailable-library handling, and the reproducibility/paths/`main()` rubric copied to agree
    with `code_critic`'s.
  - `tests/unit/nodes/llm/test_coder.py` — 21 tests (real `WorkspaceManager` against `tmp_path`;
    `LLMFactory`/`Settings` mocked at `src.nodes.llm.base`, a second `Settings` mock at
    `src.nodes.llm.coder` for the `optuna`/`mlflow` prompt injection, `execute` mocked at
    `src.nodes.llm.coder`). Covers: happy path artifact contract, both OOF conventions
    (default filename / explicit `oof_path`), whole-list `experiments` append, execution-error
    retry + stderr threading, retry exhaustion → raise, multiple-fence / missing-fence / empty
    retry paths, invalid `cv_score` / missing `submission.csv` / missing OOF / out-of-vocabulary
    `metric` / execution-timeout retry paths, a valid separator-variant metric passing without
    retry, critic-feedback threading across two invocations producing different code, config/prompt
    load, prompt content assertions (`fit_scope`, `repr(`, `json.dumps(`, "never
    string-concatenate"), a missing-`design.json` degrade path, and two tests that exercise a
    hand-written reference script through the *real* `code_executor.execute` (no LLM/Settings
    mocking): an adversarial column name (quote + backslash) surviving real execution via
    `repr()`, and an unrecognized `feature_spec.json` operation raising loudly with the operation
    named in stderr.
  - `tests/fixtures/graph_mocks.py` — added `_MOCK_CODER_TRAIN_SCRIPT` (a real, small script run
    for real by `code_executor.execute` against the smoke workspace; reads `design.json`/
    `fold_config.json`, degrades gracefully if `feature_spec.json` is absent, writes the
    three-artifact contract), registered in `_DISPATCH`, and `_seed_phase5_coder_fixtures`
    (mirrors `_seed_phase3_baseline_fixtures`: seeds `data/raw/train.csv` +
    `validation/fold_config.json`).
  - `tests/integration/phases/test_phase_subgraphs_smoke.py` — seeds Phase 5 fixtures in the
    parametrized `phase5_implementation` case and both specialist-routing tests; extended the
    phase5 assertion block to check `train.py`/`results.json`/`submission.csv`/
    `oof_predictions.parquet` on disk and `len(result["experiments"]) == 1` with the entry's
    `id`/`path`/`iteration`/`model` shape (the "one entry regardless of internal retry/critic-loop
    count" invariant).
  - `docs/pipeline.md` — added a full `coder` subsection (inputs, execution-retry loop,
    output contract, `fit_scope`/column-safety/`FORBIDDEN_CV_KEYS` handling, test patch points,
    the deliberate no-consolidated-`src/`-files scope gap), the
    experiment-directory-overwrite-on-retry convention note under `code_critic`'s
    experiment-directory-resolution paragraph, a node-classification table row, and corrected
    three stale "not yet landed"/"blocked" references (`ensemble_specialist`'s
    runtime-reachability note, `code_critic`'s "has not fixed which it records" note, Phase 6's
    "not yet landed" note, and `kaggle_client`'s "new contract pinned for coder" note in Phase 7).
  - `docs/agents.md` — added the `coder` row.
  - `context/discoveries/T-029.md` — the two discovery entries specified by the Orchestrator
    (unavailable model-family library dependencies; the deferred consolidated
    `src/features.py`/`src/models.py`/`src/train.py` scope gap).

- Deviations from plan: The Planner's `__call__` pseudocode was adjusted after checking real
  signatures: `Settings.workspace.mlflow_tracking_uri` and `Settings.optuna.n_trials`/
  `early_stopping_patience` matched exactly as guessed, so no field-name correction was needed.
  `ExecResult`'s real fields (`returncode`, `stdout`, `stderr`, `timed_out`) also matched the
  plan's assumption exactly. No `_write_output`/`_build_output_state` overrides were added (the
  base class's are unused since `__call__` is overridden wholesale, matching `code_critic`'s
  precedent). Added several extra unhappy-path unit tests beyond the Planner's required list
  (multiple-fence retry, execution-timeout retry, OOF-entirely-missing retry) to raise branch
  coverage on `_validate_run`/`_extract_code` beyond the minimum required set.

- Key decisions: `max_tokens: 8192` in `config/agents/coder.yaml` was left as specified — the
  agent-config schema/loader (`_require_field`) has no upper bound, so no adjustment was needed.
  `_oof_artifact_exists` treats a non-string/blank `results.json["oof_path"]` as "absent" and
  falls back to checking the well-known fallback filename, rather than treating it as a hard
  validation failure — matches the plan's "falls back... when that path is unset/unusable"
  framing used elsewhere in this module family (e.g. `resolve_feature_spec_ref`).

- Dependencies added: None.

### Addendum — Phase 4 review fix round (2026-08-22)

Two independent reviewers (`code-quality`, `security`) raised the same BLOCKER: neither
`design.json` nor `feature_spec.json` ever names the target column, so `coder` had no grounded
signal for which column to exclude from the feature matrix — a wrong guess produces a
plausible-looking but silently leaked/corrupted `cv_score` that can become `best_score`
(invariant #3) with `_validate_run` unable to catch it (it only checks `cv_score` is a finite
number). This is the exact T-020 failure class (`baseline_designer`/`baseline_runner`'s
target-column exclusion not being unconditional in every branch) one node downstream, except
here the column name never reached the node at all.

Fixed:
- Added `_read_target_column` (`src/nodes/llm/coder.py`), reading
  `experiments/baseline/design.json["target_column"]` — the only place `target_column` is ever
  written anywhere in this codebase, by Phase 3's `baseline_designer`, which always runs before
  Phase 5's first iteration (invariant #4), so the artifact is guaranteed present in every real
  run. Degrades to a placeholder (`"(target_column not available from
  experiments/baseline/design.json)"`) on `_experiment_design.DEGRADE_ERRORS`, on a non-dict
  payload, and on a missing/non-string/blank `target_column` field — never raises, matching
  every other reader in this module.
- Threaded it into `_build_task_message` as a new fifth `## Target column` section (both the
  function signature and its one call site in `__call__` were updated).
- Added an explicit "Target column exclusion is unconditional" section to
  `config/prompts/coder/v1.md`, mirroring T-020's exact incident (one feature-selection branch
  excluded the target, the other silently didn't) and instructing the LLM to exclude the target
  column in every code path, at the single point the feature column list is finalized. Also
  updated the "Inputs" section (four → five labeled sections) with a target-column-specific
  degrade-fallback instruction (record the fallback explicitly in `results.json` rather than
  guessing silently).
- Added 7 tests to `tests/unit/nodes/llm/test_coder.py`: target column read from
  `experiments/baseline/design.json` reaching the LLM message; degrade paths for a missing file,
  invalid JSON, a non-dict JSON value, and a dict missing/blank `target_column`; a prompt-content
  assertion for the new instruction.

Also fixed a security `WARNING` (cheap, same pass): `_oof_artifact_exists`'s workspace-containment
check never resolved symlinks before its final `.exists()` check, so a generated script could set
`oof_path` to a symlink living inside the experiment directory (passing the existing
absolute-path/`..` checks) whose target resolved outside the workspace root. Both sides are now
`.resolve()`d and checked with `Path.is_relative_to` (Python 3.10, per `pyproject.toml`'s
`requires-python = ">=3.10"`) before the artifact is treated as present. Added
`test_oof_artifact_symlink_escaping_workspace_is_rejected` and a companion
`test_oof_artifact_symlink_within_workspace_is_accepted` to confirm a same-workspace symlink is
still accepted.

Did not touch: `config/prompts/timeseries_specialist/v1.md` (out of scope, T-027's), `code_critic`
(a separate node's blind spot, not blocking this PR), the deferred consolidated
`src/features.py`/`src/models.py`/`src/train.py` scope, or the two INFO-level findings
(prompt-injection-via-column-names, hand-written-vs-real-LLM test coverage) — all per the
Orchestrator's explicit instructions for this fix round.

Verification: `pytest --cov=src --cov-fail-under=70 -x` → 2133 passed, 97.42% coverage;
`ruff check . && ruff format --check .` → all checks passed, 141 files already formatted;
`mypy src/` → no issues found in 78 source files. Commit `a117d6d`, pushed to
`feature/T-029-node-coder`.

### Addendum — Phase 4 review round 2 fix (2026-08-31)

Three independent reviewers (`code-quality`, `smoke-tester` — BLOCKED — and `adversarial`
converging separately) found that round 1's OOF symlink-containment fix only covered the
explicit-`oof_path` branch of `_oof_artifact_exists`. The far more common fallback branch (no
`oof_path` key in `results.json`, just the well-known `oof_predictions.parquet` filename in
`exp_dir` — the path `test_oof_predictions_default_convention` itself exercises) still called
bare `.exists()` with no `.resolve()`/containment check, so a generated script could place a
symlink at the fallback filename pointing outside the workspace root and have it reported as a
present artifact.

Fixed:
- Restructured `_oof_artifact_exists` so there is structurally only one containment check left
  in the function, reached by both paths: it now first computes a single `candidate: Path` —
  either the re-relativized `oof_path` string (if usable) or `Path(exp_dir) /
  _OOF_FALLBACK_FILENAME` (if not) — and *then* runs the shared `..`-rejection /
  `.resolve()` / `Path.is_relative_to` / `.exists()` sequence once against whichever candidate
  was chosen. No duplicated logic remains that could drift out of sync again the way round 1's
  partial fix did.
- Added `test_oof_artifact_fallback_filename_symlink_escaping_workspace_is_rejected` and
  `test_oof_artifact_fallback_filename_symlink_within_workspace_is_accepted` to
  `tests/unit/nodes/llm/test_coder.py` — the fallback-path mirrors of the two existing
  explicit-`oof_path` symlink tests, using `results = {"cv_score": 0.9}` (no `oof_path` key) so
  the code actually exercises the previously-unguarded branch.

Also fixed both WARNING-level findings from the same review round:
- `docs/pipeline.md`'s `coder` "Inputs" paragraph still said "four labeled sections" and omitted
  round 1's `## Target column` addition. Updated to five sections with a brief note on the
  target-column input, its placeholder-on-degrade behavior, and a cross-reference to
  `config/prompts/coder/v1.md`'s "Target column exclusion is unconditional" section rather than
  duplicating that contract in the architecture doc.
- Added `test_baseline_design_blank_target_column_degrades_to_placeholder` and
  `test_baseline_design_non_string_target_column_degrades_to_placeholder` to
  `tests/unit/nodes/llm/test_coder.py`, covering the two `_read_target_column` degrade paths
  (whitespace-only string, non-string value) that were previously exercised by the
  implementation but not asserted by any test.

Did not touch: `context/decisions/T-029.md` (populated post-merge by the Orchestrator, per
`coder-complete.md`'s steering rule — not a PR-branch artifact), `code_critic.py` or either
specialist prompt (other tasks' files, out of scope), or the deferred consolidated
`src/features.py`/`src/models.py`/`src/train.py` scope — all per the Orchestrator's explicit
instructions for this fix round.

Verification: `pytest --cov=src --cov-fail-under=70 -x` → 2137 passed, 97.42% coverage;
`ruff check . && ruff format --check .` → all checks passed, 141 files formatted; `mypy src/` →
no issues found in 78 source files. Commit `3bb16cf`, pushed to `feature/T-029-node-coder`.

### Addendum — Phase 4 adversarial review round 3 fix (2026-08-31)

Adversarial review (round 3, run after the three parallel reviewers found nothing further) found
3 genuine issues. Two required fixes in this PR; two more are advisory, written as new open
discovery entries instead.

**Finding 1 (HIGH) — no cleanup between execution-retry attempts, fixed.** `coder`'s own
execute-then-re-prompt loop (`_MAX_EXECUTION_RETRIES`) never cleared `experiments/exp_{iteration}/`
between attempts. Each attempt's generated script independently decided which of the three
contract artifacts to write, and `_validate_run` only checked "does the right-named file exist and
parse correctly right now" — never "was it written by *this* attempt's execution." Concretely: an
attempt could write `submission.csv` but fail on a different check and retry; the next,
LLM-regenerated attempt could fix that check but have its own unrelated bug and never reach its own
`submission.csv` write — `_validate_run`'s bare `.exists()` would then silently accept the *stale*
`submission.csv` from the earlier attempt alongside the new attempt's fresh `results.json`/OOF,
recording an internally inconsistent artifact triplet as a successful run. This is exactly the
`results.json` that `score_evaluator` later reads as ground truth for `best_score`/
`best_experiment_path` (CLAUDE.md invariant #3).

Fixed by adding `_clear_contract_artifacts(workspace, exp_dir)`, called immediately before every
attempt's `execute()` call (including the first, to handle stale state from a wholly separate prior
graph invocation of the same iteration, e.g. after a process restart). It deletes the three
well-known filenames (`results.json`, `submission.csv`, `oof_predictions.parquet`) from `exp_dir`
if present, ignoring `FileNotFoundError` (via `contextlib.suppress`, per `ruff`'s SIM105). This
establishes the invariant that whatever `_validate_run` finds for attempt N either doesn't exist or
was written by attempt N's own `execute()` call.

**Accepted residual limitation, documented in `_clear_contract_artifacts`'s docstring rather than
closed with a full mtime-based solution:** a *custom* `results.json["oof_path"]` named by a
previous attempt is not proactively deleted by name, since that name is only knowable after that
attempt's own `execute()` has already run (cleaning it would require having already read the very
`results.json` this call is about to delete — a chicken-and-egg problem). Judged narrower than it
sounds and disproportionate to close fully: Finding 3's exp_dir-scoping fix (below) already
requires any `oof_path` candidate to resolve inside the *same* `exp_dir`, so a survivor must
already be a same-experiment file, not an arbitrary workspace path. The well-known-filename case —
the common/default path, and the one the required test exercises — is fully closed.

Added `test_retry_clears_stale_artifacts_between_attempts` to `tests/unit/nodes/llm/test_coder.py`:
attempt 0 writes `submission.csv` but fails on an invalid `cv_score`; attempt 1 asserts the stale
`submission.csv` is already gone before its own `execute()` runs, and deliberately does not write a
new one; attempt 2 succeeds fully. Confirms 3 LLM invocations and a final, freshly-written
`submission.csv`.

**Finding 3 (MEDIUM) — OOF `oof_path` branch scoped to the workspace root, not `exp_dir`, fixed.**
`_oof_artifact_exists`'s explicit-`oof_path` branch (the one round 2 fixed for symlink containment)
only required the resolved candidate to stay inside the workspace root — not inside the *current*
experiment's own `exp_dir`, unlike the fallback branch (exp_dir-scoped by construction) and unlike
the `submission.csv` check elsewhere in the same function. So `results.json["oof_path"]` could
legitimately name any pre-existing file anywhere else in the workspace — no traversal or symlink
needed — e.g. a different experiment's own real OOF file, or `validation/fold_config.json`.

Fixed by replacing the workspace-root containment check with an exp_dir-scoped one
(`resolved.is_relative_to((workspace.workspace_path / exp_dir).resolve())`), applied uniformly to
both the `oof_path` and fallback candidates (they already shared one code path after round 2's
fix, so this is a one-line change reached by both). While in the function, also changed the final
`.exists()` to `.is_file()` (round 3 security reviewer's smaller robustness note): a resolved
candidate that exists but is a directory was previously reported as "present."

Added `test_oof_artifact_explicit_oof_path_outside_exp_dir_is_rejected` (a real, non-symlinked,
in-workspace file outside `exp_dir` is now rejected) and
`test_oof_artifact_oof_path_pointing_at_directory_is_rejected` (a resolved candidate that is a
directory is rejected). Both existing symlink-containment tests still pass unmodified since their
symlink targets already live inside `exp_dir`.

**Finding 2 (report_writer staleness) and Finding 4 (code_executor sandboxing) — written as
discoveries, not fixed here (out of scope: different modules/tasks).** Appended two new `## OPEN`
entries to `context/discoveries/T-029.md`: one addressed to `report_writer.py`'s owner (T-033)
noting it trusts a cached `cv_score` in `state["experiments"]` that goes stale whenever
`code_critic` forces an `iterate` cycle — unlike `score_evaluator`, which already re-reads
`results.json` fresh from disk via each entry's `path`; one addressed to infra-agent noting
`code_executor.execute()` has no OS-level sandboxing beyond stripping credential env vars, and
`coder` is the first node combining `implementation`-role LLM output with real subprocess/
filesystem execution reach.

Did not touch: `report_writer.py`, `code_executor.py` (other tasks'/agents' files, per the
Orchestrator's explicit instructions for this fix round), or anything beyond Findings 1 and 3.

Verification: `pytest --cov=src --cov-fail-under=70 -x` → 2140 passed, 97.43% coverage;
`ruff check . && ruff format --check .` → all checks passed, 141 files formatted; `mypy src/` →
no issues found in 78 source files. Commits `b96ad70` (findings 1+3 fix),
`df53685` (discoveries + this addendum), pushed to `feature/T-029-node-coder`.
