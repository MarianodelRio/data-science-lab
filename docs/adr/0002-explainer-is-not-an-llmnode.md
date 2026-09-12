# ADR 0002 — The explainer is a read-only service, not an `LLMNode`

**Date:** 2026-09-12
**Status:** Accepted
**Author:** Architect (dev-team)

## Context

T-036 adds the chat explainer (`src/api/explainer.py`), described in `design.md`
as a "separate LangGraph subgraph" with **read-only** access to `LabState`,
workspace files and the RAG store, reachable over `WS /api/runs/{id}/chat`.

Two existing constructs could plausibly host it:

- `src/nodes/llm/base.py::LLMNode` — the base class every LLM-calling pipeline
  node subclasses. Its `__call__` unconditionally calls
  `WorkspaceManager.write_text(...)` via `_write_output` and returns a state
  delta containing `{"messages": [response]}`. Both are disqualifying: the
  explainer must never write to the workspace and must never contribute to the
  `add_messages` channel of a run that may be executing concurrently.
  `LLMNode` is also invoked by `GraphBuilder` through node discovery by
  convention (`src/nodes/{llm,compute}/{name}.py`), which the explainer is not
  part of — it is driven by a WebSocket handler, not by a phase.
- A one-node `StateGraph`. This buys nothing (no branching, no checkpointing, no
  interrupt) while pulling LangGraph runtime construction into `src/api/`, which
  otherwise depends only on the injected `CompiledGraphLike` protocol
  (`src/api/main.py`) — the seam that lets the whole API test suite run with a
  fake graph and no LangGraph/sqlite dependency.

## Decision

The explainer is a plain class in `src/api/explainer.py` with a synchronous
entry point (`answer()`); the caller offloads it via `asyncio.to_thread` rather
than the class itself being async, since every blocking call inside it —
LLM invocation, workspace reads, the RAG query — needs the same offload
regardless of the entry point's own signature. It obtains its model through
`LLMFactory.get(config.model_role)` and its
system prompt through `PromptLoader`, driven by `config/agents/explainer.yaml`
(`model_role: reasoning`) and `config/prompts/explainer/v1.md` — so model and
prompt stay external to Python per CLAUDE.md's modularity principle and
invariant #7. `output_file_pattern` in that YAML is the empty string and is
never read: the field is required by the `AgentConfig` dataclass (a protected
contract), and leaving it empty is preferable to widening the contract for a
single non-writing consumer.

"Separate LangGraph subgraph" in `design.md` is read as *separate from the main
pipeline graph*, not as a mandate to construct a `StateGraph`. A `StateGraph`
implementation is permitted but not required; the read-only and no-`LabState`-
mutation constraints below bind either way.

Read-only is enforced structurally, not by convention: the explainer is
constructed with a `WorkspaceManager` and a state-snapshot reader, and it never
receives a write path. Forwarding a human interrupt decision (`human_feedback`
+ resume) is **not** the explainer's job — that is performed by the chat router
through the same shared helper the REST `POST /api/runs/{id}/resume` uses, so
the sentence "the explainer never mutates state" stays literally true and
directly testable.

## Consequences

- The pattern for any future LLM-calling code outside the pipeline graph
  (a second chat agent, a summarizer behind an endpoint) is: plain class, config
  YAML + prompt file, no `LLMNode` inheritance, no `StateGraph` wrapper.
- `src/api/` gains a dependency on `src/llm/` and `src/config/` — both shared
  contracts, both already permitted. It gains no dependency on `src/nodes/` or
  `src/graph/`; the module DAG stays acyclic.
- Two files land outside `src/api/` (`config/agents/explainer.yaml`,
  `config/prompts/explainer/v1.md`), in directories nominally owned by
  `pipeline-agent`. Both are new leaf files that no pipeline task reads: node
  discovery resolves `config/agents/*.yaml` only for names listed in a
  `config/phases/*.yaml` node list, and no test enumerates the agents directory.
  T-036's `folders:` is extended to those two exact paths rather than moving the
  work to a second task.
- The cost is a second place where "an agent" is defined. Accepted: the
  alternative is either a workspace-writing explainer or a hardcoded model role.
