# Pipeline Reference

Primary architecture doc for the LangGraph pipeline. Updated by the coder on every task that
adds/changes a node, a phase, or a pipeline-level contract. See `design.md` for full rationale;
this file tracks the current implemented state.

## State

> Skeleton — populated when `src/state.py` lands (T-002). Document the `LabState` TypedDict
> fields (input, file pointers, control, scores, experiment index, checkpoint) and any
> state-mutation rules.

## Graph topology

> Skeleton — populated when `src/graph/` lands. Document the main graph diagram (phase sequence,
> interrupt points, the phase4↔phase6 iteration loop), the supervisor conditional edge, and the
> SQLite checkpointer setup.

## The 7 phases

> Skeleton — one subsection per phase (`Understanding`, `Research`, `Baseline`, `Design`,
> `Implementation`, `Evaluation`, `Delivery`), each documenting its nodes, sequence, critic, and
> interrupt behavior. Populated incrementally by each phase's implementing task.

## Node classification

> Skeleton — table of LLM nodes vs pure Python nodes vs tools, populated as each node lands.

## Tools

> Skeleton — one subsection per tool (`code_executor`, `kaggle_client`, `rag`,
> `workspace_manager`), populated by the task that implements each tool.

## RAG

> Skeleton — vector store, embeddings, indexing pipeline, and retrieval pattern, populated by the
> RAG tool task.

## Observability

> Skeleton — local JSONL logs, MLflow experiment tracking, and opt-in LangSmith tracing, populated
> by the observability task.

## Invariants

> Skeleton — the pipeline-level invariants enforced by this codebase (fold immutability, single
> file-I/O point, best-score-only updates, retry caps, etc.), populated as each is implemented and
> enforced in code. See `design.md` § Critical invariants for the current target list.
