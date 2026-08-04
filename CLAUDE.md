# Data Science Lab — CLAUDE.md

## What this project is

A multi-agent system built on LangGraph that takes a problem statement + dataset
and produces a **complete, reproducible ML project repository**. Specialized AI
agents collaborate across seven phases — Understanding, Research, Baseline,
Design, Implementation, Evaluation, Delivery — iterating autonomously with human
checkpoints, and learning from their own experiments via a persistent RAG store.

Built and coordinated with the **dev-team** framework (parallel spec-driven
development). Single source of truth: git.

See `design.md` for the full architecture and `plan.md` for the task graph.

---

## Two repositories

| Repo | What it is |
|---|---|
| `data-science-lab/` (this repo) | The agent system: LangGraph pipeline, FastAPI backend, React UI |
| `~/competitions/{name}/` | The ML repo built **by** the agents — the deliverable |

The agent system never mixes its own code with the workspace it generates.

---

## Module ownership (project agents)

Each agent writes **only** inside its folders (defined in `.claude/agents/`).

| Agent | Owns |
|---|---|
| `infra-agent` | `src/state.py`, `src/config/`, `src/llm/`, `src/tools/`, `src/workspace/`, `src/memory/`, `src/observability/`, root scaffold, `docker/`, `.github/` |
| `pipeline-agent` | `src/graph/`, `src/nodes/`, `config/agents/`, `config/phases/`, `config/prompts/`, `docs/pipeline.md`, `docs/agents.md` |
| `api-agent` | `src/api/`, `docs/api.md` |
| `frontend-agent` | `frontend/` |

**Protected contracts** (require explicit human approval before change):
`src/state.py` (LabState), `src/config/` dataclasses, `LLMFactory.get` signature,
`WorkspaceManager` public API, `RagStore`/`IndexDocument` schema,
`config/settings.yaml`, `config/phases/*.yaml`, docker/CI config.

---

## Modularity — the core design principle

Agents, phases, prompts, and model choices are **external to the Python code**.
Changing behavior means editing config, not code:

- Add/remove an agent → `config/agents/{name}.yaml` + `config/prompts/{name}/v1.md` + `src/nodes/…/{name}.py`; register it in a phase's node list
- Change a prompt → edit `config/prompts/{agent}/v1.md` or add `v2.md` and bump `prompt_version`
- Change a model → edit `config/settings.yaml` → `models.{role}.model`
- Node discovery is by convention (GraphBuilder imports `src/nodes/{llm|compute}/{name}.py` by name) — **no central registry file, so parallel node tasks never conflict**

---

## Critical invariants (never violate)

1. `validation/fold_config.json` is **write-once** — frozen after Pipeline Phase 1
2. `WorkspaceManager` is the **sole** file-I/O point to the workspace
3. `best_experiment_path`/`best_score` update **only** on improvement
4. Baseline (Pipeline Phase 3) runs **only** at `current_iteration == 0`
5. Critics enforce `max_critic_retries` then force `pass` — no infinite loops
6. Max 2 LLM agents run concurrently (Pipeline Phase 2 only)
7. Prompts live in `config/prompts/`, never inline in Python
8. Compute nodes never import an LLM module

---

## Model routing (config/settings.yaml)

| Role | Provider / Model | Used by |
|---|---|---|
| `advisor` | Anthropic Opus 5 | high-risk architecture decisions |
| `reasoning` | DeepSeek V4 Flash | architect, feature_engineer, specialists, evaluation LLMs |
| `implementation` | DeepSeek V4 Flash | coder, code_critic, baseline_designer |
| `research` | DeepSeek V3.2 | researchers, report_writer |
| `fast` | Groq Llama 4 (free tier) | critics, problem_framer, memory_manager |

Target cost: **< $0.50 per full competition run.**

---

## Task lifecycle

```
available/ → in-progress/ → pr-open/ → done/
```

Status lives in each task file's YAML frontmatter; folder = visual signal.
State transitions go through `scripts/` (`dt-claim`, `dt-ready`, `dt-done`,
`dt-cancel`, `dt-restart`) — not hand-rolled git. `dt-board` regenerates the
git-ignored `.dt-index.json` cache.

### Task file format

```markdown
---
id: T-001
phase: 0
agent: infra-agent
depends_on: []
status: available
folders: [src/...]
outputs: [...]
size: S
branch: ~
pr: ~
---

## Title
**Scope:** ...
**Delivers:** ...
**Done when:** measurable checklist incl. tests + primary doc (docs/pipeline.md)
```

After completing a task, append a `## Completed` section (what was implemented,
what changed, decisions and why).

---

## Branch policy

```
main                          ← always stable, all checks passing
feature/T-XXX-short-slug      ← one branch per task
fix/B-XXX-short-slug          ← one branch per bug
```

- Never commit directly to `main` except `tasks/` status metadata
- Each agent works in its own worktree (`../data-science-lab-T-XXX/`)
- Branch creation = task claim; push race lost → task already taken
- `main` must **not** be a protected branch (framework pushes `tasks/*.md` to main)

---

## Quality gates (every PR)

**Tests** — types per the Testing strategy in `design.md`; critical modules
(`quality.critical_modules`) held to ≥85% coverage; global ≥70%. Unit tests in
the same PR. No network calls in unit tests; fixtures in `tests/fixtures/`.

**Code quality** — `ruff check . && ruff format --check .`, `mypy src/` pass;
frontend `npm run lint && npm run build`. No secrets, debug prints, or dead code.

**Architecture** — no imports outside owned folders; no business logic in HTTP
handlers; protected contracts untouched (or explicitly approved).

**Documentation** — new pipeline stage/node → `docs/pipeline.md`; new agent →
`docs/agents.md`; new endpoint → `docs/api.md`; config change → `docs/configuration.md`;
architectural decision → ADR in `docs/adr/`.

---

## Context files

- `context/decisions.md` — log non-obvious technical decisions
- `context/discoveries.md` — cross-agent alerts (found something affecting another module → note it here, don't touch that module)

`git pull origin main --ff-only` before appending (these are append-only).

---

## Commands

| Command | What it does |
|---|---|
| `/team-init` | Configure and show current state — run first |
| `/orchestrate [T-XXX]` | Pick next task, analyze, plan, code, review, open PR |
| `/bug [description]` | Investigate a bug → fix task |
| `/explore [topic]` | Investigate behavior in the project |
| `/done T-XXX` | Mark DONE after merge, report unblocked tasks |
| `/add-task [description]` | Add a task mid-project |
| `/status` · `/cheatsheet` · `/guide` | Board, next-step, and state views |
| `/restart T-XXX` · `/cancel T-XXX` | Recover / abandon a task |

---

## Rules agents must follow

1. Never write outside assigned `folders:`
2. Never modify a protected contract without explicit human approval
3. Always write tests in the same PR (LLM/network mocked in unit tests)
4. Always log non-obvious choices in `context/decisions.md`
5. Always check `context/discoveries.md` before implementing
6. Planner plans, Coder codes — escalate out-of-role needs to the Orchestrator
7. Never skip the human checkpoint
8. Update the task with a `## Completed` section after READY_FOR_PR
