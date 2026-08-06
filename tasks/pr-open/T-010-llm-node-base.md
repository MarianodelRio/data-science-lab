---
id: T-010
phase: 1
agent: pipeline-agent
depends_on: [T-002, T-004, T-005]
status: pr-open
folders: ["src/nodes/"]
outputs: [LLMNode base class, context trimming, standard node lifecycle]
size: M
branch: feature/T-010-llm-node-base
pr: "https://github.com/MarianodelRio/data-science-lab/pull/13"
---

## LLM node base + convention registry (src/nodes/llm/base.py)

**Scope:** `src/nodes/` base classes only. **Shared contract** — every LLM node subclasses this.

**Delivers:**
- `LLMNode` base class: constructor loads its `AgentConfig` (by class-declared `name`), gets its model via `LLMFactory.get(config.model_role)`, loads its prompt via `PromptLoader`
- `__call__(state: LabState) -> dict` lifecycle: trim context (`last_n_messages` per `settings.context`), build messages, invoke LLM, write output via `WorkspaceManager`, return the state delta (path + any scalars)
- Context trimming helper honoring `max_messages_per_node`
- Convention: a node lives at `src/nodes/llm/{name}.py` exposing a class whose `name` matches the config filename (so GraphBuilder can import by name — no central registry)

**Done when:**
- [x] a sample subclass declaring `name="dummy"` loads its config + prompt and, with a mocked LLM, returns a state delta
- [x] context trimming keeps only the last N messages
- [x] the node writes its output through `WorkspaceManager` (asserted via mock), never directly
- [x] LLM is mocked — no network calls
- [x] `mypy src/nodes/llm/base.py` passes
- [x] `docs/agents.md` "Adding an agent" section updated

## Completed

Implemented `LLMNode` (`src/nodes/llm/base.py`) as a plain class (not `pydantic.BaseModel`,
to stay compatible with `node_resolver`'s zero-arg `cls()` instantiation and its
`getattr(obj, "name", None)` class-attribute lookup). Constructor loads `AgentConfig` via
`load_agent_config(self.name, base_dir=...)`, the model via `LLMFactory.get(config.model_role)`,
and the system prompt via `PromptLoader`; `WorkspaceManager` is deliberately **not** held on
`self` — it's constructed inside `__call__` from `state["workspace_path"]` per call, since
`__init__` must stay zero-argument-instantiable.

`__call__` trims `state["messages"]` via the standalone `trim_context(messages, max_messages_per_node)`
helper (settings.context.max_messages_per_node; special-cases `n <= 0` since Python's
`messages[-0:]` returns the full list, not `[]`), builds the message list, invokes the LLM,
writes the response through `WorkspaceManager` at `output_file_pattern` resolved via
`_resolve_output_path`, and returns `{"messages": [<new response only>]}` merged with whatever
the subclass's `_build_output_state` hook adds — `LabState.messages` is the only
`add_messages`-reducer field, so nodes must never re-return accumulated history.

Four override points give concrete subclasses (T-013+) their extension surface without
touching the base: `_build_messages` (inject extra input beyond system prompt + history),
`_resolve_output_path` (non-iteration placeholders), `_write_output` (e.g. `workspace.write_json`
for structured output), `_build_output_state` (set the node's own `LabState` path field).

Adversarial review caught two real bugs in `_resolve_output_path`, fixed across two commits:
`output_file_pattern.format(iteration=...)` silently ignores a missing `{iteration}` placeholder
(Python `str.format` behavior) and raises a bare `KeyError` on an extra/unknown placeholder. The
first fix attempt over-corrected by requiring `{iteration}` in every pattern at construction time —
caught in a second orchestrator review pass and reverted, since most planned node outputs
(`fold_config.json`, `eda_report.md`, `leakage_audit.json`, `final_report.md`, etc., per
design.md's workspace layout and CLAUDE.md invariant #1) are intentionally one-time/frozen, not
per-iteration. Final behavior: a missing `{iteration}` is valid (fixed path, by design); only a
genuinely unresolved/extra placeholder raises, as a clear `ValueError` naming the agent and
pattern instead of a bare `KeyError`.

Rebasing onto main after T-011 (`ComputeNode` base) merged produced one mechanical conflict in
`tests/unit/graph/test_builder.py`'s `test_no_real_node_modules_exist_yet` — both tasks
independently added the identical `("__init__", "base")` exclusion logic, differing only in
docstring wording; resolved by keeping the already-merged T-011 wording, which covers both base
classes.

Test fixtures added under `tests/fixtures/config/agents/` and `tests/fixtures/prompts/` (not
real `config/`, which doesn't exist on disk yet per the T-003 decision). 274 tests pass repo-wide,
100% coverage on `base.py`, mypy/ruff clean.
