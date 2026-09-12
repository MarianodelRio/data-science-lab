---
inclusion: agentMatch
agents: [orchestrator]
---

# Review pipeline — Phase 4 procedure

**This steering file is different from the other four.** `always.md`, `task-format.md`,
`context-formats.md` and `coder-complete.md` are relayed by the Orchestrator into sub-agent
prompts. This one is **read and executed by the Orchestrator itself** — it is the procedure the
Orchestrator follows in `/orchestrate` Phase 4 and `/prepare-pr` Step 4 to run the review. There is
no `review-coordinator` agent; the Orchestrator spawns the reviewers directly as background
sub-agents so it (and the user at the terminal) can see, inspect and stop each one.

The Orchestrator already holds every input this procedure needs, from Phase 0 config extraction and
the Phase 1 context packet:

| Input | Source |
|---|---|
| `PR_DIFF` | `git diff origin/main` from the feature branch (captured in Phase 4 before this procedure) |
| `task_file` | the full task file |
| `code_quality_slice` | Module list/DAG + Testing strategy + Documentation plan (Phase 1; may be empty in escape-hatch mode) |
| `spec_sections` | spec.md module sections for the task's modules (Phase 1; empty if spec.md absent or no match) |
| `touches_protected` | `true` if Phase 1 Architect reported protected files or contract changes |
| `CFG_REVIEW_PROFILE` | `quality.review_profile` — `full` \| `fast` \| `auto` |
| `CFG_SMOKE_TEST_MODE` | `quality.smoke_test_mode` — `sandbox` \| `live` |
| `CFG_PROJECT_TYPE`, `CFG_PROJECT_STACK` | `project.type`, `project.stack` |
| `CFG_CMD_RUN` (and install/start if configured) | `commands.run` / `commands.install` / `commands.start` |
| `CFG_REQUIRE_MUTATION_TESTS`, `CFG_CRITICAL_MODULES`, `CFG_MUTATION_SCORE_THRESHOLD` | `quality.*` |
| `CFG_SPEC_COVERAGE_ENABLED`, `CFG_SPEC_COVERAGE_THRESHOLD` | `quality.*` |
| `CFG_REVIEW_TIMEOUT_MIN` | `orchestration.review_timeout_minutes` (default 15) |
| `STEERING_ALWAYS`, `STEERING_TASK_FORMAT` | loaded in Phase 0 — forwarded inline to each reviewer |

The `worktree` path (`../$PROJECT_SLUG-T-XXX/`) is where reviewers write their full reports.

---

## Step 1 — Resolve the effective profile

1. `touches_protected: true` → force `full` regardless of `CFG_REVIEW_PROFILE`.
2. `CFG_REVIEW_PROFILE: full` → `full`.
3. `CFG_REVIEW_PROFILE: fast` → `fast`.
4. `CFG_REVIEW_PROFILE: auto`:
   - Inspect `PR_DIFF` for changed file extensions.
   - All changed files are docs or config (`.md`, `.yml`, `.yaml`, `.json`, `.toml`, `.env*`, `.txt`) → `fast`.
   - Any changed file is source code or tests → `full`.

Record `effective_profile: full | fast` and `profile_reason: [why]`.

**Agent set by profile:**

| Agent | fast | full |
|---|---|---|
| code-quality | yes | yes |
| security | yes | yes |
| smoke-tester | no | yes |
| mutation-tester | no | conditional (see below) |
| adversarial | no | yes |
| spec-coverage | conditional | conditional |

**spec-coverage (both profiles):** run when `CFG_SPEC_COVERAGE_ENABLED: true`. Independent of profile
— it is text analysis, not code execution.

**mutation-tester (full profile only):** run when either is true:
- `CFG_REQUIRE_MUTATION_TESTS: true`
- Any path in `CFG_CRITICAL_MODULES` appears in `PR_DIFF`

---

## Step 2 — Reviewer registration pre-flight

Claude Code only registers a file in `.claude/agents/` as a spawnable type when its frontmatter
declares both `name` (equal to the filename) and `description`. Verify before spawning:

```bash
for a in code-quality security adversarial smoke-tester mutation-tester spec-coverage; do
  f=".claude/agents/$a.md"
  [ -f "$f" ] && grep -q "^name: $a$" "$f" && grep -q "^description: " "$f" || echo "UNREGISTERED: $a"
done
```

- `code-quality` or `security` unregistered → **halt the review**. Report to the user:
  `REVIEW BLOCKED — required agent not registered: [name] (.claude/agents/[name].md is missing, or
  its frontmatter lacks name/description). Manual review required.` Do not run a partial review.
- `adversarial`, `smoke-tester` or `mutation-tester` unregistered → log a warning, skip that agent,
  record it in the report. Never substitute a generic agent.
- `spec-coverage` unregistered (only checked when `CFG_SPEC_COVERAGE_ENABLED: true`) → log a warning,
  skip it, add this line to the manifest: `[SCOV-VERDICT] NOT_RUN (spec-coverage agent not registered)`

---

## Step 3 — Spawn the parallel reviewers as background sub-agents

Spawn every active reviewer **directly, as a background sub-agent** (`run_in_background: true`), in
one batch — do not wait for one before launching the next. Prepend `STEERING_ALWAYS` and
`STEERING_TASK_FORMAT` inline to each reviewer's prompt.

The parallel set is at most 5 (code-quality, security, smoke-tester, mutation-tester, spec-coverage),
which matches `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS: 5` in `.claude/settings.json`. `adversarial`
runs after this batch (Step 5), so it never competes for a slot.

Give every reviewer the **worktree path** (`../$PROJECT_SLUG-T-XXX/`) and tell it, in its prompt:

> **Return to the Orchestrator only:** your `### Verdict` line, your findings as manifest-ready
> lines (`file:line — one-line description (severity)`), and a summary of at most 3 sentences. Write
> your full narrative report to `<worktree>/.dt-review/<your-agent-name>.md` (create the directory if
> needed). Do not return the full narrative inline.

`mutation-tester` and `smoke-tester` run commands inside the worktree — they need that path and must
`cd` into it. `code-quality`, `security` and `spec-coverage` work from the diff text alone.

Per-reviewer inputs:

**code-quality** — `PR_DIFF`, `task_file`, `code_quality_slice` (as-is; may be empty).

**security** — `PR_DIFF`, `task_file`.

**smoke-tester** (full only) — the task's "Done when" checklist, `CFG_SMOKE_TEST_MODE`,
`CFG_PROJECT_TYPE`, `CFG_PROJECT_STACK`, `commands.install`, `commands.start` (or `CFG_CMD_RUN`).

**mutation-tester** (if activated) — `PR_DIFF`, `CFG_CRITICAL_MODULES`, `CFG_MUTATION_SCORE_THRESHOLD`.

**spec-coverage** (when `CFG_SPEC_COVERAGE_ENABLED: true`) —
- `spec_sections` (full module sections; the agent focuses on Logic and Interface)
- `test_diff` — sections of `PR_DIFF` whose file path contains `/test`, `/tests/`, `/__tests__/` or
  `/spec/` as a path component, or whose filename starts with `test_` or ends with `_test.{ext}`,
  `.test.{ext}`, `.spec.{ext}`. Empty string if none match.
- `task_file`
- `CFG_SPEC_COVERAGE_THRESHOLD`

### Collecting results — per-reviewer deadline

Wait for the reviewers to return. Track each one. If a reviewer has not returned after
`CFG_REVIEW_TIMEOUT_MIN` minutes (default 15) while others have completed:

1. `TaskStop` that reviewer.
2. Record `[<AGENT>]: TIMEOUT — no output returned` in the manifest.
3. Continue with the results you have.

The deadline is best-effort — the real safeguard is that these are direct background sub-agents, so
you and the user can inspect (`TaskOutput`) or stop them at any time. Never block Phase 4
indefinitely on a single reviewer.

If a reviewer returns an error or empty output, treat it the same way:
`[<AGENT>]: failed — no output returned`.

Collect all results before Step 4.

---

## Step 4 — Build the compact findings manifest

One line per finding, exact formats below.

**Finding ID format:** deterministic 8-char hash — `sha1(file_path + ':' + line_number + ':' +
summary[0:20])` truncated to 8 hex chars. Stable across re-runs without persistent state. Example:
`CQ-3a9f7c12`.

**code-quality** — one line per finding in its Issues section:
```
[CQ-{hash8}] file:line — one-line description (severity: BLOCKER|WARNING|NITPICK)
```
Verdict:
```
[CQ-VERDICT] APPROVED | BLOCKED: N blockers | WARNINGS: N warnings
```

**security** — one line per finding:
```
[SEC-{hash8}] file:line — one-line description (severity: BLOCKER|WARNING|INFO)
```
Verdict:
```
[SEC-VERDICT] CLEAN | WARNINGS: N | BLOCKED: N blockers
```

**smoke-tester:**
```
[SMOKE] verdict: ALL PASS (X/Y) | BLOCKED (X/Y failed) — [failed criterion names if any]
```

**mutation-tester (if run):**
```
[MUT] score: X% (threshold: Y%) — STRONG | WEAK | NOT_RUN
```
If not run: `[MUT] NOT_RUN`

**spec-coverage (when `CFG_SPEC_COVERAGE_ENABLED: true`)** — one line per UNCOVERED or PARTIAL:
```
[SCOV-{hash8}] {module_name}:spec — {constraint_text_50_chars} (UNCOVERED|PARTIAL) (severity: INFO)
```
`{module_name}:spec` replaces `file:line` because these findings reference spec requirements, not
code. SCOV hashes use `sha1(module_name + ':spec:' + text[0:20])`.
Verdict:
```
[SCOV-VERDICT] ADVISORY: X% (Y/Z covered) | WARN_LOW: X% (Y/Z covered) | NOT_APPLICABLE | NOT_RUN
```
If not run (`CFG_SPEC_COVERAGE_ENABLED: false`): `[SCOV-VERDICT] NOT_RUN (spec_coverage_enabled: false)`

Example manifest:
```
[CQ-3a9f7c12] src/auth/login.ts:42 — bare catch swallows exceptions (severity: BLOCKER)
[CQ-8b4d1e2f] src/auth/login.ts:67 — function exceeds 50 lines (severity: NITPICK)
[SEC-c5f0a8b3] src/auth/token.ts:15 — JWT secret hardcoded (severity: BLOCKER)
[SEC-d2e1f9c4] src/api/users.ts:88 — missing rate limit on endpoint (severity: WARNING)
[SCOV-a1b2c3d4] auth:spec — When login fails, the system shall return... (UNCOVERED) (severity: INFO)
[CQ-VERDICT] BLOCKED: 1 blocker | WARNINGS: 0
[SEC-VERDICT] BLOCKED: 1 blocker | WARNINGS: 1
[SMOKE] verdict: ALL PASS (3/3)
[MUT] score: 82% (threshold: 80%) — STRONG
[SCOV-VERDICT] WARN_LOW: 60% (3/5 covered)
```

---

## Step 5 — Adversarial (full profile only)

Skip entirely if `effective_profile: fast`.

Extract all finding IDs from the manifest (`CQ-{hash8}`, `SEC-{hash8}`, `SCOV-{hash8}`).

Spawn `adversarial` **sequentially** (after the parallel reviewers, not in the Step 3 batch), as a
direct sub-agent, with:
- The full `PR_DIFF`
- The compact findings manifest from Step 4 (never the reviewers' full outputs)
- Appended instruction:
  > "The following findings have already been reported — do not duplicate them: [comma-separated
  > finding IDs]. Find what they missed."

Prepend `STEERING_ALWAYS` and `STEERING_TASK_FORMAT` as for the other reviewers, and give it the
same return-small / spill-to-`.dt-review/adversarial.md` instruction.

Extract adversarial findings:
```
[ADV-{hash8}] file:line — one-line description (severity: HIGH|MEDIUM|LOW)
```
Verdict:
```
[ADV-VERDICT] CLEAN | FLAWS FOUND: N findings
```
Append both to the manifest.

---

## Step 6 — Consolidated report and Overall verdict

Assemble this report (used to drive the blocker-classification rubric in `/orchestrate` Phase 4 /
`/prepare-pr` Step 5, and referenced by finding ID in PR bodies and Coder retry messages):

```
## Consolidated Review Report — T-XXX

### Profile
Effective: [full|fast]
Requested: [CFG_REVIEW_PROFILE]
[Override reason if forced — e.g. "forced full: diff touches protected file src/contracts/models.py"]

### Code Quality
[code-quality verdict line + findings + 3-sentence summary; full report at .dt-review/code-quality.md]

### Security
[security verdict line + findings + summary; full report at .dt-review/security.md]

### Smoke Tests
[smoke-tester verdict + summary, or "NOT RUN (fast profile)"]

### Mutation Testing
[mutation-tester verdict + summary, or "NOT RUN (fast profile or not activated)"]

### Spec Coverage
[spec-coverage verdict + findings, or the NOT_RUN line]

### Adversarial
[adversarial verdict + findings + summary, or "NOT RUN (fast profile)"]

### Findings manifest
[Complete compact manifest — all finding lines and all VERDICT lines]

### Overall verdict
APPROVED — no blockers across all review agents
  (Guard: valid only if code-quality AND security returned actual results. If either failed, timed
   out, or produced no output, use BLOCKED below.)
or
BLOCKED — manual review required: [agent] did not return results.
or
BLOCKED — N total blockers requiring fixes:
  - [CQ-3a9f7c12] src/auth/login.ts:42 — bare catch swallows exceptions
  - [SEC-c5f0a8b3] src/auth/token.ts:15 — JWT secret hardcoded
  [Optional warnings — PR can open with these flagged:]
  - [SEC-d2e1f9c4] src/api/users.ts:88 — missing rate limit
or
WARNINGS ONLY — no blockers; PR can open with warnings flagged:
  - [SEC-d2e1f9c4] ...
```

---

## Rules

- **Never approve or block the PR here** — produce the verdict; the Orchestrator's Phase 4 rubric
  decides retry and merge policy.
- **Never fix code** — this procedure reports only.
- **Never run adversarial on the fast profile** — cost and signal don't justify it.
- **Never pass full reviewer outputs to adversarial** — only the compact manifest.
- **Never run mutation-tester outside its activation condition** — it is expensive.
- **Spawn the parallel reviewers as background sub-agents** — sequential spawning of the Step 3 batch
  is a defect; you lose the visibility this design exists to provide.
- **Always include the full manifest** in the consolidated report — finding IDs are referenced in PR
  bodies, blocker escalations and Coder retry messages.
- **SCOV findings are advisory** — `WARN_LOW` never affects the Overall verdict. BLOCKED and
  WARNINGS ONLY are determined by CQ, SEC, SMOKE, MUT and ADV findings only.
- **Required-agent guard** — an APPROVED verdict requires both code-quality and security to have
  returned actual findings. If either failed, timed out, or returned nothing, the Overall verdict is
  BLOCKED.
- **A timed-out reviewer is not a pass** — record it, and let the required-agent guard turn a missing
  code-quality/security into BLOCKED.
