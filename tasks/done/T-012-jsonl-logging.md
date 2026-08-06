---
id: T-012
phase: 1
agent: infra-agent
depends_on: [T-002]
status: done
folders: ["src/observability/"]
outputs: [JsonlCallbackHandler writing runs/{run_id}/execution.jsonl]
size: S
branch: feature/T-012-jsonl-logging
pr: "https://github.com/MarianodelRio/data-science-lab/pull/14"
---

## JSONL logging callback (src/observability/)

**Scope:** `src/observability/` only. Observability layer 1 (always on).

**Delivers:**
- `JsonlCallbackHandler(run_id)` — a LangGraph/LangChain callback that appends one JSON line per node entry/exit to `runs/{run_id}/execution.jsonl`
- Each line matches the schema in `design.md` § Observability: `{timestamp, run_id, iteration, phase, node, event, duration_ms, tokens_in, tokens_out, model, output_summary}`
- Never raises into the pipeline: logging failures are swallowed with a stderr warning

**Done when:**
- [x] handler writes one line on node start and one on node end
- [x] each line is valid JSON containing all schema keys
- [x] `duration_ms` is populated on the end event
- [x] a write failure (e.g. bad path) does not propagate an exception
- [x] tests use `tmp_path`
- [x] `docs/pipeline.md` "Observability" section updated

## Completed

Implemented `JsonlCallbackHandler(run_id, runs_dir=None)` in `src/observability/jsonl_callback.py`
as a `langchain_core.callbacks.BaseCallbackHandler` subclass. Key decisions:

- `run_id` validated at construction (rejects empty, `.`/`..`, path separators) — raises
  `ValueError` synchronously since a malformed `run_id` is a caller bug, not a runtime logging
  failure that should be swallowed.
- `iteration`/`phase`/token usage/model are captured via LangChain callback hooks
  (`on_chain_start/end`, `on_chat_model_start`, `on_llm_start`/`on_llm_end`), correlated across
  nested phase subgraphs and node/LLM-call pairs via LangChain's `run_id`/`parent_run_id`
  bookkeeping — no changes needed to `src/graph/` or `src/nodes/` (LangChain's ambient
  `RunnableConfig` propagation carries callbacks through automatically).
- `phase` is derived from LangGraph's `langgraph_checkpoint_ns` metadata (the phase subgraph
  currently on the call stack) rather than `LabState["phase"]`, which is only stamped *after* a
  phase subgraph finishes (`_wrap_phase` in `src/graph/builder.py`) and would otherwise be one
  phase stale for every node. Falls back to `inputs.get("phase")` when no LangGraph metadata is
  present (e.g. direct/unit-test invocation).
- LangGraph-internal plumbing runs (the outer graph's own `.invoke()`, and each phase subgraph's
  own top-level `.invoke()` inside `_wrap_phase`) are filtered out via a positive signal
  (`metadata["langgraph_node"] == name`) so only genuine registered nodes produce log lines.
- Token fields use `None`/absent-call semantics distinctly from `0`/zero-tokens: a bucket only
  ever reports `0` if a real extracted value was `0`, never as a default for "couldn't extract."
- `output_summary` is a best-effort 200-char-truncated string (last LLM message content, or
  `"updated: {keys}"` for compute nodes) — not redacted; a `context/decisions.md` entry flags
  this as an unaddressed latent secret-leak risk for whichever future task feeds tool/subprocess
  output through `LabState.messages`.
- Not wired up to any real graph invocation yet (no caller attaches
  `config={"callbacks": [handler]}`) — this task delivers the handler standalone, tested directly
  against LangChain's callback interface plus two integration tests using real `StateGraph`
  topologies mirroring `_wrap_phase`'s exact wiring.

Review caught and fixed two real bugs during the adversarial pass (reproduced against a real
LangGraph graph mirroring `builder.py`'s topology, not just unit-level mocks): `phase` staleness
and spurious `"LangGraph"`-named log lines from internal plumbing runs. Both required one Coder
retry after initial review; adversarial testing also refuted the code-quality reviewer's
concurrency-corruption concern (8000 concurrent writes, zero corruption with this file-append
pattern) and confirmed `parent_run_id` token correlation is safe for this codebase's LLM-call
pattern.
