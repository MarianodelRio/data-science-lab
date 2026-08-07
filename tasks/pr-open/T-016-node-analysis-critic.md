---
id: T-016
phase: 2
agent: pipeline-agent
depends_on: [T-010]
status: pr-open
folders: ["src/nodes/llm/", "config/agents/", "config/prompts/"]
outputs: [analysis_critic node, pass/iterate verdict with feedback, retry guard]
size: S
branch: feature/T-016-node-analysis-critic
pr: "https://github.com/MarianodelRio/data-science-lab/pull/18"
---

## Node: analysis_critic (Pipeline Phases 1 & 4)

**Scope:** `analysis_critic` `LLMNode` + agent YAML + prompt.

**Delivers:**
- Reviews analytical outputs (EDA, problem framing, solution plan, feature spec) against a methodological-rigor rubric
- Returns a structured verdict `{verdict: "pass"|"iterate", feedback: str, target_node: str}`
- Enforces `max_critic_retries` (from settings, default 3): after N iterate cycles on the same target, forces `pass` and records that the retry budget was exhausted
- `model_role: fast`

**Done when:**
- [x] mock LLM returning `iterate` yields a verdict with non-empty `feedback` and a `target_node`
- [x] after `max_critic_retries` iterate verdicts on one target, the node forces `pass` (test drives the counter)
- [x] `verdict` is always one of `pass`/`iterate`
- [x] agent YAML + prompt v1 exist and load
- [x] unit tests with mocked LLM, no network
- [x] `docs/agents.md` row added

## Completed

Implemented `AnalysisCriticNode` (`src/nodes/llm/analysis_critic.py`), the shared critic for both
Pipeline Phase 1 (Understanding) and Phase 4 (Design). It fully overrides `LLMNode.__call__`
instead of composing via the base class's hooks, since — per the T-009 decision — critics own
their own retry control flow internally, re-invoking target nodes directly through
`src.graph.node_resolver.resolve_node` rather than via graph conditional edges (`LabState` gets no
new fields).

Phase identity is detected via `_detect_phase_stem(state)` (`bool(state.get("feature_spec_path"))`),
since `state["phase"]` is only stamped by the graph after a phase subgraph finishes and is stale at
the moment `analysis_critic` itself runs (documented inline and in a `context/decisions.md` entry).
`max_retries` and the phase's `targets` are read from `load_phase_config(phase_stem).critic`, not
`Settings.execution.max_critic_retries`, since the per-phase field is the one actually threaded
through `config/phases/*.yaml`. Retry counts are local variables inside `__call__` (no `LabState`
counter field). A global `max_total_cycles` safety cap guards CLAUDE.md invariant #5 against a
pathological LLM naming a different target every cycle, though pigeonhole reasoning shows the
per-target guard always fires first for the currently configured targets/retries — documented as
defense-in-depth rather than currently-reachable.

Resolves the open T-015 → T-016 discovery: `validation_strategist` raises `FoldsAlreadyFrozenError`
unconditionally on any second invocation once `validation/fold_config.json` exists (write-once per
invariant #1), and nothing in `src/graph/` catches exceptions around node execution. `analysis_critic`
now catches this exception specifically when retrying `validation_strategist` (re-raises for any
other target) and forces a pass with `folds_frozen: True` instead of crashing the graph.

Verdict parsing never raises on malformed LLM output — always normalizes to `pass`/`iterate` with
non-empty feedback, falls back to `allowed_targets[0]` for an out-of-set `target_node`. Fence-stripped
JSON extraction tries multiple candidate strips (raw, last-fence-anchored, first-fence-anchored) to
survive embedded/stray backtick fences in feedback text.

**Review-driven fix (round 1):** `output_file_pattern` was initially
`"reports/critic_verdicts_iter{iteration}.json"`, varying only by `current_iteration` — since
`analysis_critic` runs once at the end of Phase 1 and once at the end of Phase 4 within the same
`current_iteration == 0`, Phase 4's write silently clobbered Phase 1's audit trail. Fixed by adding
`{phase}` to the pattern (`"reports/critic_verdicts_{phase}_iter{iteration}.json"`) and overriding
`_resolve_output_path` locally to interpolate it. Also scoped the `FoldsAlreadyFrozenError` catch to
`target_node == "validation_strategist"` specifically (re-raises otherwise), and added a
`context/decisions.md` entry documenting that the phase-detection heuristic relies on checkpoints
being forward-only (per the existing 2026-08-06 decision) — not reachable today, flagged for
whoever revisits that decision.

381→385 tests passing repo-wide (96% coverage), `ruff`/`mypy` clean. PR:
https://github.com/MarianodelRio/data-science-lab/pull/18
