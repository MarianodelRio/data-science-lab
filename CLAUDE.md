# Data Science Lab — CLAUDE.md

## What this project is

A multi-agent system built on LangGraph that takes a problem statement + dataset
and produces a **complete, reproducible ML project repository**. Specialized AI
agents collaborate across seven phases — Understanding, Research, Baseline,
Design, Implementation, Evaluation, Delivery — iterating autonomously with human
checkpoints, and learning from their own experiments via a persistent RAG store.

Built and coordinated with the **dev-team** framework (parallel spec-driven
development). Single source of truth: git.

Reference documents:

| File | What it holds |
|---|---|
| `design.md` | Intended architecture: shared contracts, LangGraph topology, the seven phases |
| `spec.md` | What each module **actually does today** — logic, interface, out-of-scope, cross-module flows. Generated brownfield from the code; where it and `design.md` disagree, `spec.md` follows the implementation and says so. **Edit only via `/refine`** |
| `plan.md` | The task graph |

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

Cross-cutting rules for the framework agents live in `.claude/steering/`. Their
`inclusion:` frontmatter declares the intended scope (`always.md` and `task-format.md` →
all; `context-formats.md` → orchestrator/architect/coder/planner; `coder-complete.md` →
coder), but **there is no harness-side injection**: the Orchestrator reads these files in
Phase 0 and pastes the relevant ones inline at the top of each sub-agent prompt.
`.claude/AGENTS.md` is only a stub pointing there.

### Agent file format

Every file in `.claude/agents/` **must** declare `name` and `description` in its
frontmatter. Claude Code registers a file as an invocable sub-agent type only when both
are present — a file carrying just `model:` is silently ignored, the spawn falls back to a
generic agent with the definition pasted inline, and per-agent model routing is lost.
Nothing reports an error, so treat the frontmatter as load-bearing, not metadata.

```markdown
---
name: pipeline-agent        # exactly the filename without .md
description: One line — what the agent owns and when to invoke it.
model: claude-sonnet-5
---
```

`name` must match the filename character for character: every sub-agent spawn and every
`agent:` field in `tasks/*.md` resolves by that string. Nested spawning
(review-coordinator → its reviewers) additionally needs
`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH: "2"` in `.claude/settings.json`.

Check every agent file at once:

```bash
for f in .claude/agents/*.md; do n=$(basename "$f" .md); \
  grep -q "^name: $n$" "$f" && grep -q "^description: " "$f" || echo "BROKEN: $f"; done
```

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
State transitions go through `scripts/` — not hand-rolled git:

| Script | Transition |
|---|---|
| `dt-claim` | `available/` → `in-progress/` (creates branch + worktree) |
| `dt-pr` | `in-progress/` (or `ready-for-pr/`) → `pr-open/`; opens the PR, tears down the worktree |
| `dt-done` | `pr-open/` → `done/` after merge |
| `dt-cancel` · `dt-restart` | abandon / recover a task |
| `dt-ready` | `in-progress/` → `ready-for-pr/` — **escape hatch only**; the normal path goes straight through `dt-pr` |

`dt-verify` runs test + lint + type-check against a worktree (used by `/orchestrate`
before and after review). `dt-board` regenerates the git-ignored `.dt-index.json` cache.

**Who moves the task file, and who edits it.** The `tasks/*/` folder moves belong to `main`
alone — `dt-claim`, `dt-ready`, `dt-pr` and `dt-done` rename the file there as status
changes. The Coder only appends `## Completed`, **in place**, at whatever folder the file
currently sits in (`git ls-files 'tasks/*/T-XXX.md'` inside the worktree). It never creates
the file at another path and never moves it: a second copy at the folder `main` is using
becomes an `add/add` conflict at the Phase 4 rebase. `dt-claim` fast-forwards the feature
branch onto its own claim commit precisely so both sides see one path.

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
- Never move a task file from a feature branch — the `tasks/*/` moves are main's; a branch
  only appends `## Completed` in place
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

**Spec coverage** — when `quality.spec_coverage_enabled` is on, the `spec-coverage`
agent maps `spec.md` constraints to tests in the diff. Advisory: it reports
`WARN_LOW` below `spec_coverage_threshold` but **never blocks a PR**.

**Documentation** — new pipeline stage/node → `docs/pipeline.md`; new agent →
`docs/agents.md`; new endpoint → `docs/api.md`; config change → `docs/configuration.md`;
architectural decision → ADR in `docs/adr/`.

---

## Context files

One file **per task**, not one flat log (dev-team v1.1+):

- `context/decisions/T-XXX.md` — non-obvious technical decisions taken in that task
- `context/discoveries/T-XXX.md` — cross-agent alerts (found something affecting another
  module → note it here, don't touch that module). `Status: open` is load-bearing: the
  Orchestrator surfaces only open entries to the Architect and Planner
- `context/retrospectives/{coder,planner,architect}.md` — lessons extracted by `/done`
  and injected back in `/orchestrate` Phase 0. Written by the framework, not by hand

Create the file if it doesn't exist; `git pull origin main --ff-only` before appending.
The exact entry formats live in `.claude/steering/context-formats.md` — that file is the
source of truth and the Orchestrator injects it into the agents that need it.

**Sub-agents never read `context/` directly** — architect, planner, coder, advisor and
every reviewer receive only what was handed to them in their prompt. **The Orchestrator is
the exception:** it reads `context/decisions/` and `context/discoveries/` itself to perform
that pre-selection (decisions filtered by folder, discoveries filtered to open entries).

History note: entries predating the v1.4 migration live in `context/decisions/` under
their task id, plus `general.md` (entries with no task id) and `legacy-header.md`. All
pre-migration discoveries are in `context/discoveries/legacy.md`.

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
| `/refine [change]` | Edit `spec.md` safely, propagating by task status — **the only way to change `spec.md`; never edit it by hand** |
| `/reopen T-XXX` | Move a task from `pr-open/` back to `available/` when its PR is rejected or closed unmerged |
| `/status` · `/cheatsheet` · `/guide` | Board, next-step, and state views |
| `/restart T-XXX` · `/cancel T-XXX` | Recover / abandon a task |

---

## Rules agents must follow

1. Never write outside assigned `folders:`
2. Never modify a protected contract without explicit human approval
3. Always write tests in the same PR (LLM/network mocked in unit tests)
4. Always log non-obvious choices in `context/decisions/T-XXX.md`
5. Always check open entries in `context/discoveries/` before implementing
6. Planner plans, Coder codes — escalate out-of-role needs to the Orchestrator
7. Never skip the human checkpoint
8. Update the task with a `## Completed` section after READY_FOR_PR
