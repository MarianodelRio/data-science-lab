# Data Science Lab — Implementation Plan

46 tasks across 6 build phases. Tasks are small (S/M), independent, and heavily
parallelizable. Each pipeline-node task creates its own `config/agents/*.yaml`,
`config/prompts/*/v1.md`, and node implementation — no shared-file edits between
parallel tasks.

> **Build Phase** = framework build stage (this document).
> **Pipeline Phase** = one of the 7 runtime phases of the product (see `design.md`).

---

## Project agents and ownership

| Agent | Owns | Tasks |
|---|---|---|
| `infra-agent` | `src/state.py`, `src/config/`, `src/llm/`, `src/tools/`, `src/workspace/`, `src/memory/`, `src/observability/`, root scaffold, `docker/`, CI | T-001..T-008, T-012, T-043, T-044 |
| `pipeline-agent` | `src/graph/`, `src/nodes/`, `config/agents/`, `config/phases/`, `config/prompts/` | T-009..T-011, T-013..T-033, T-045, T-046, T-047 |
| `api-agent` | `src/api/` | T-034..T-037 |
| `frontend-agent` | `frontend/` | T-038..T-042 |

Phase composition (`config/phases/*.yaml`) is created **once** by T-009 with the
full node list per phase. Node tasks never edit a shared phase YAML — the
GraphBuilder discovers each node by importing `src/nodes/{llm|compute}/{name}.py`
by convention. This keeps all node tasks conflict-free.

---

## Build Phase 0 — Foundations & Contracts

Shared contracts. Small but they block most of the tree.

| Task | Title | Size | Depends |
|---|---|---|---|
| T-001 | Project scaffold | S | — |
| T-002 | LabState contract | S | T-001 |
| T-003 | Config system + settings.yaml | M | T-001 |
| T-004 | LLMFactory + provider wrappers | M | T-003 |
| T-005 | WorkspaceManager | M | T-001 |

## Build Phase 1 — Tools, Graph Skeleton & Node Bases

| Task | Title | Size | Depends |
|---|---|---|---|
| T-006 | code_executor tool | S | T-001 |
| T-007 | kaggle_client tool | S | T-001 |
| T-008 | rag tool + Chroma + embeddings | M | T-001 |
| T-009 | GraphBuilder + supervisor + phase YAMLs + checkpointer | M | T-002, T-003 |
| T-010 | LLM node base + convention registry | M | T-002, T-004, T-005 |
| T-011 | Compute node base + conditional edges | S | T-002 |
| T-012 | JSONL logging callback | S | T-002 |

## Build Phase 2 — Pipeline Nodes (heavily parallel)

| Task | Node(s) | Size | Depends |
|---|---|---|---|
| T-013 | data_analyst | M | T-010, T-006 |
| T-014 | problem_framer + leakage_auditor | S | T-010 |
| T-015 | validation_strategist (freezes folds) | M | T-010 |
| T-016 | analysis_critic | S | T-010 |
| T-017 | literature_researcher + web_researcher | M | T-010, T-008 |
| T-018 | competition_analyst | S | T-010, T-007, T-008 |
| T-019 | memory_manager | S | T-010, T-008 |
| T-020 | baseline_designer + baseline_runner | M | T-010, T-011, T-006 |
| T-021 | solution_architect | S | T-010, T-008 |
| T-022 | feature_engineer | S | T-010 |
| T-023 | specialist_selector | S | T-011 |
| T-024 | classical_ml_specialist | S | T-010 |
| T-025 | deep_learning_specialist | S | T-010 |
| T-026 | nlp_specialist | S | T-010 |
| T-027 | timeseries_specialist | S | T-010 |
| T-028 | ensemble_specialist | S | T-010 |
| T-029 | coder | M | T-010, T-006 |
| T-030 | code_critic | S | T-010 |
| T-031 | score_evaluator + feature_importance_extractor | M | T-011 |
| T-032 | error_analyst + hypothesis_generator + experiment_designer | M | T-010, T-008 |
| T-033 | reviewer + report_writer + kaggle_client node | M | T-010, T-007 |
| T-047 | feature_spec.json v2 — single primitive + `fit_scope` | M | T-022 |

## Build Phase 3 — API

| Task | Title | Size | Depends |
|---|---|---|---|
| T-034 | FastAPI skeleton + run management | M | T-009 |
| T-035 | SSE event stream + asyncio.Queue emitter | M | T-034 |
| T-036 | WebSocket chat + explainer agent | M | T-034, T-010 |
| T-037 | Kaggle submit + MLflow URL endpoints | S | T-034, T-007 |

## Build Phase 4 — Frontend

| Task | Title | Size | Depends |
|---|---|---|---|
| T-038 | React scaffold + layout + API client | M | — |
| T-039 | PipelineView (SSE) | M | T-038 |
| T-040 | ExperimentsTable | S | T-038 |
| T-041 | Chat component (WebSocket) | M | T-038 |
| T-042 | FileViewer + ActionBar | S | T-038 |

## Build Phase 5 — Integration & Delivery

| Task | Title | Size | Depends |
|---|---|---|---|
| T-043 | docker-compose + Dockerfiles + services | M | T-034, T-038 |
| T-044 | CI workflow | S | T-001 |
| T-045 | Docs skeletons | S | — |
| T-046 | End-to-end smoke test | M | T-013..T-033, T-034, T-035 |

---

## Dependency analysis

**Critical path:**
```
T-001 → T-003 → T-004 → T-010 → T-029 (coder) → T-046 (smoke)
```
5 real sequential steps + final integration.

**Available at start (no dependencies):** T-001, T-038, T-045

**Minimum time (max parallelism):** ~6 waves
**Time with no parallelism:** 46 sessions
**Maximum parallel tasks at peak:** ~21 (all pipeline nodes, Wave 5)

**Waves:**
- **Wave 1:** T-001, T-038, T-045
- **Wave 2:** T-002, T-003, T-005, T-006, T-007, T-008, T-039, T-040, T-041, T-042, T-044
- **Wave 3:** T-004, T-009, T-011, T-012
- **Wave 4:** T-010, T-034 → T-035, T-036, T-037
- **Wave 5:** T-013..T-033 (21 pipeline nodes, fully parallel)
- **Wave 6:** T-043, T-046
- **Post-wave (added mid-project):** T-047 (follow-up on T-022)

**Parallel-but-sequenced check:** none — the dependency graph is already
optimally parallel. All same-wave tasks share no dependency between them.

---

## Full dependency graph (adjacency)

```
T-001 → T-002, T-003, T-005, T-006, T-007, T-008, T-044
T-002 → T-009, T-010, T-011, T-012
T-003 → T-004, T-009
T-004 → T-010
T-005 → T-010
T-006 → T-013, T-020, T-029
T-007 → T-018, T-033, T-037
T-008 → T-017, T-018, T-019, T-021, T-032
T-009 → T-034
T-010 → T-013..T-022, T-024..T-030, T-032, T-033, T-036
T-011 → T-020, T-023, T-031
T-022 → T-047
T-034 → T-035, T-036, T-037, T-043
T-038 → T-039, T-040, T-041, T-042, T-043
(T-013..T-033, T-034, T-035) → T-046
```
