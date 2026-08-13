---
id: T-026
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: pr-open
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [nlp_specialist node, experiment design with Optuna search space]
size: S
branch: feature/T-026-node-nlp-specialist
pr: "https://github.com/MarianodelRio/data-science-lab/pull/27"
---

## Node: nlp_specialist (Pipeline Phase 5)

**Scope:** `nlp_specialist` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Designs text experiments: TF-IDF baselines, sentence-transformer embeddings, optional fine-tuning; with an Optuna search space
- Writes `experiments/exp_{next_id}/design.json`; activated only when text features exist
- `model_role: reasoning`

**Done when:**
- [x] with a mocked LLM the node writes an experiment `design.json` containing a `search_space` and a text-based `model_family`
- [x] the design references the frozen folds
- [x] agent YAML + prompt v1 exist and load
- [x] unit test with mocked LLM, no network
- [x] `docs/agents.md` row added

## Completed

Implemented `nlp_specialist`, the Phase 5 text/NLP specialist node, as a structural mirror of
`classical_ml_specialist` (T-024) reusing the shared `design.json` contract in
`_experiment_design.py`. Per the human checkpoint correction, this node does **not** self-gate on
"text features exist" — `specialist_selector`'s NLP-keyword branch (T-023) is the only route to it.

**Files added:**
- `src/nodes/llm/nlp_specialist.py` — `NlpSpecialistNode(LLMNode)`, `name = "nlp_specialist"`.
  Overrides `_build_messages` (injects `## Solution plan` / `## Frozen CV folds` / `## Feature spec
  reference`, degrading each to a placeholder rather than raising) and `_write_output` (extracts +
  validates the JSON payload via the shared `_experiment_design` functions, writes via
  `workspace.write_json`). Does not override `_build_output_state` — `coder` (T-029) reads
  `design.json` from its well-known path. `_MODEL_FAMILIES` recognizes exactly three canonical
  tokens: `tfidf_linear`, `sentence_embeddings`, `transformer_finetune` (human checkpoint decision).
- `config/agents/nlp_specialist.yaml` — `model_role: reasoning`, `prompt_version: v1`,
  `output_file_pattern: "experiments/exp_{iteration}/design.json"`.
- `config/prompts/nlp_specialist/v1.md` — system prompt: three canonical `model_family` tokens, the
  `ngram_range`-as-string-token convention (never a JSON tuple/array-of-two), text-specific
  preprocessing vocabulary, the `FORBIDDEN_CV_KEYS` + HF `Trainer`-arg early-stopping guidance for
  `transformer_finetune`, and v2 feature-spec-ref phrasing (T-047 vocabulary — no
  encodings/null_handling/interactions language).
- `tests/unit/nodes/llm/test_nlp_specialist.py` — 33 tests, 100% coverage of
  `src/nodes/llm/nlp_specialist.py`: config/prompt load, output path, injected fields, frozen-CV
  rejection (parametrized over `FORBIDDEN_CV_KEYS`), prompt assembly + all four upstream-degrade
  paths, model-family alias normalization (parametrized over all three families), unsupported and
  **ambiguous** (two families named) `model_family` rejection, and `_write_output`-before-
  `_build_messages` ordering.

**Files modified:**
- `src/nodes/llm/_experiment_design.py` — added `read_solution_plan` (hoisted verbatim from
  `classical_ml_specialist._read_solution_plan`, now the third copy of that reader — T-024's
  decision log pre-approved the hoist at that threshold); `classical_ml_specialist`'s own copy is
  deliberately left in place (already shipped/tested). Updated the module docstring's landed/pending
  specialist list.
- `tests/unit/nodes/llm/test_experiment_design.py` — added a `read_solution_plan` test block
  mirroring `read_fold_summary`'s (unset path, `OSError`, malformed JSON, invalid UTF-8,
  path-outside-workspace, traversal path, pathological nesting, non-string path, happy path).
- `tests/unit/nodes/compute/test_specialist_selector.py` — added
  `test_real_resolve_node_resolves_landed_nlp_specialist` (real, un-mocked `resolve_node`, asserts
  `NlpSpecialistNode` not `NoOpNode`); updated the surrounding comment to name `nlp_specialist` as
  landed.
- `docs/agents.md` — new row for `nlp_specialist`.
- `docs/pipeline.md` — new `nlp_specialist` bullet (calls out the shared `read_solution_plan` reuse
  vs. `classical_ml_specialist`'s node-local copy), the "beyond ... below" fallback-behavior
  sentence, the `_experiment_design.py` landed/pending refresh, and a new Node classification row.
- `context/decisions.md` — one entry: the three-canonical-family choice, the
  `experiments/exp_{iteration}/design.json` path-scheme ruling (with "exactly one specialist per
  iteration" recorded as the explicit invariant it relies on), the `ngram_range` prompt convention,
  and the `read_solution_plan` hoist rationale.
- `context/discoveries.md:312` — the "All five Phase-5 specialists write the same path" entry
  marked `RESOLVED`, with a resolution note pointing at the `decisions.md` entry above.

**Deviations from the plan:** none in scope or file list. One correction made during implementation:
the plan's alias table for `transformer_finetune` (`"bert finetune"`, `"distilbert finetune"`, ...)
did not match the plan's own worked example `"DistilBERT fine-tuning"` (three separate words after
separator-normalization, vs. the one-word `finetune` aliases) — added `"...fine tuning"` variants
alongside the existing `"...finetune"` aliases for `transformer`/`bert`/`distilbert` so that example
resolves as specified. Re-verified no cross-family alias collisions after the addition (all 18
aliases round-trip through `normalize_model_family` to their own canonical key).

**Verification (pre-fix):** `pytest --cov=src --cov-fail-under=70` — 933 passed (96.84% total
coverage), 1 deselected (`test_resume_after_restart_does_not_rerun_completed_phase` — pre-existing
failure on `origin/main` before this task's changes, documented at `context/discoveries.md:236`,
unrelated to `nlp_specialist`/`_experiment_design.py`). `src/nodes/llm/nlp_specialist.py` — 100%
coverage (26/26 lines). `ruff check .` / `ruff format --check .` / `mypy src/` — all pass with zero
issues.

## Post-checkpoint fix: alias-table ambiguity gap (adversarial review)

Adversarial review found that `_MODEL_FAMILIES["transformer_finetune"]` didn't catch a fine-tune
modifier combined with a bare `sentence_embeddings` alias — e.g. `"fine-tuned sentence
transformer"`, `"SBERT fine-tuning"` — because `normalize_model_family` has no longest-match-wins
rule and only raises "ambiguous" when two *complete* alias phrases are both literally present.
Without a matching `transformer_finetune` alias, these phrases silently resolved to
`sentence_embeddings` alone, discarding the modifier and writing a `design.json` whose
`model_family` would contradict its own `rationale`. This class of bug doesn't exist in
`classical_ml_specialist` (distinct model brands, no modifier axis) — it's specific to
`nlp_specialist`'s frozen-vs-fine-tuned distinction.

**Fix (local to `nlp_specialist.py`, per the review's explicit boundary — `_experiment_design.py`
untouched):** added six bare fine-tune-modifier tokens to `_MODEL_FAMILIES["transformer_finetune"]`:
`"fine tune"`, `"fine tuned"`, `"fine tuning"`, `"finetune"`, `"finetuned"`, `"finetuning"`. Any of
these co-occurring with a `sentence_embeddings` (or `tfidf_linear`) alias now makes both families
match, routing into the already-existing "ambiguous" rejection instead of a silent
misclassification. Chose bare tokens over the eight specific paired-combo aliases the review
suggested as a minimum, because three of its six adversarial examples have an intervening word, a
comma, or a pluralized noun phrase between the modifier and the family term — literal paired
aliases cannot reach those regardless of how many are added, while the bare tokens are a strict
superset that covers all eight suggested combos and all six adversarial phrases. Full reasoning and
the round-trip re-verification in `context/decisions.md` (2026-08-12, second T-026 entry).

Also strengthened `config/prompts/nlp_specialist/v1.md`: `model_family` must be the bare literal
token, never prose describing the approach — defense in depth for this class of deviation.

Filed `context/discoveries.md` (2026-08-12, OPEN, addressed to T-025/T-027/T-028 and whoever owns
`normalize_model_family`): the alias-table fix is a local mitigation, not a fix — the general
solution (longest-match-wins or similar) belongs in the shared `normalize_model_family`, out of
scope for a single node task, and every sibling specialist whose families sit on a similar modifier
axis should not re-solve this locally.

**Re-verification:**
- Full alias round-trip re-checked: every original alias across all three families, plus each new
  bare modifier token in isolation, still resolves solely to its own family (no new unintended
  collisions). All 8 combo phrases the review suggested as a minimum, and all 6 of its adversarial
  examples, now raise ambiguous as required. `"DistilBERT fine-tuning"` regression still resolves
  cleanly to `transformer_finetune` alone.
- Added `test_fine_tune_modifier_on_sentence_embeddings_alias_raises_ambiguous`
  (`tests/unit/nodes/llm/test_nlp_specialist.py`), parametrized over the review's six adversarial
  phrases, asserting each raises `ValueError` matching "ambiguous" and nothing is written.
- `pytest --cov=src --cov-fail-under=70` (no `-x`) — 939 passed, 1 failed
  (`test_resume_after_restart_does_not_rerun_completed_phase`, the same pre-existing
  `origin/main` failure noted above — confirmed the only failure in the run), 96.92% total
  coverage.
- `src/nodes/llm/nlp_specialist.py` — still 100% coverage (39 tests now, was 33).
- `ruff check .` / `ruff format --check .` / `mypy src/` — all pass with zero issues.
