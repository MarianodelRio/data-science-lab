You are the Orchestrator running /prepare-pr in escape hatch mode.

⚠️ This command is an escape hatch. In the normal dev-team v2 flow,
reviewers run automatically at the end of /orchestrate.
Use this only for tasks that reached ready-for-pr manually
(tasks migrated from v1, recovered orphaned worktrees, etc.).

Input: $ARGUMENTS — task ID (T-XXX or B-XXX)

Your job: complete the review and open the PR for a task that is already
implemented in its branch.

---

## Step 1 — Verify task

Read `tasks/ready-for-pr/T-XXX-slug.md` (or find it if not in ready-for-pr/).

If the task is not in `status: ready-for-pr`, warn:
```
⚠️ T-XXX is currently in [status], not ready-for-pr.
Do you want to prepare the PR anyway?
```
Wait for explicit confirmation.

---

## Step 2 — Rebase

```bash
git fetch origin
git checkout feature/T-XXX-short-slug
git rebase origin/main
```

If there are conflicts:
- Mechanical (whitespace, unrelated imports): resolve alone
- Design (contracts, business logic, schema): stop and present to the user:
  ```
  ⚠️ Design conflict in [file:line]
  
  In main ([T-YYY already merged]):
  [code]
  
  In this branch (T-XXX):
  [code]
  
  This implies [concrete trade-off]. How should we resolve it?
  ```
  Wait for direction. Apply. Continue rebase.

---

## Step 3 — Verification

```bash
bash scripts/dt-verify.sh
```

If anything fails: report the specific error and stop. Do not fix behavioral failures automatically.

---

## Step 3b — Extract config

```bash
source scripts/dt-common.sh
CFG_REVIEW_PROFILE=$(dt_config quality.review_profile "auto")
CFG_PR_MODE=$(dt_config workflow.pr_mode "automatic")
CFG_REQUIRE_MUTATION_TESTS=$(dt_config quality.require_mutation_tests "false")
CFG_CRITICAL_MODULES=$(dt_config quality.critical_modules "")
CFG_MUTATION_SCORE_THRESHOLD=$(dt_config quality.mutation_score_threshold "80")
CFG_SMOKE_TEST_MODE=$(dt_config quality.smoke_test_mode "sandbox")
CFG_PROJECT_TYPE=$(dt_config project.type "")
CFG_PROJECT_STACK=$(dt_config project.stack "")
CFG_CMD_RUN=$(dt_config commands.run "")
CFG_SPEC_COVERAGE_ENABLED=$(dt_config quality.spec_coverage_enabled "false")
CFG_SPEC_COVERAGE_THRESHOLD=$(dt_config quality.spec_coverage_threshold "80")
CFG_REVIEW_TIMEOUT_MIN=$(dt_config orchestration.review_timeout_minutes "15")
```

Load steering content:
```bash
STEERING_ALWAYS=$(cat .claude/steering/always.md 2>/dev/null || echo "")
STEERING_TASK_FORMAT=$(cat .claude/steering/task-format.md 2>/dev/null || echo "")
STEERING_REVIEW_PIPELINE=$(cat .claude/steering/review-pipeline.md 2>/dev/null || echo "")
```

---

## Step 4 — Review (run the review pipeline yourself)

There is no `review-coordinator` agent. You run the review pipeline directly, spawning the reviewers
as background sub-agents. Follow `STEERING_REVIEW_PIPELINE` (`.claude/steering/review-pipeline.md`,
loaded in Step 3b) exactly.

Inspect the diff for protected files or shared contracts:

```bash
git diff --name-only origin/main
```

Set `touches_protected` = `true` if any changed file is a protected file or shared contract.

Read `design.md` and extract `code_quality_slice` (three sections: Module DAG, Testing strategy, Documentation plan). If `design.md` is absent (escape-hatch mode for migrated tasks), set `code_quality_slice` to empty.

If `$CFG_SPEC_COVERAGE_ENABLED` is `true` and `spec.md` exists:
  Read `spec.md` and extract the spec sections for the task's modules (the module sections whose folders overlap with the task's `folders:` frontmatter). Set `SPEC_SECTIONS` to the Logic and Interface subsections of those modules. If no matching sections are found, set `SPEC_SECTIONS` to empty.
Else:
  Set `SPEC_SECTIONS` to empty.

Then run the pipeline steps from `STEERING_REVIEW_PIPELINE`:

1. **Resolve the effective profile** from `$CFG_REVIEW_PROFILE` and `touches_protected` (protected
   files / contracts force `full`).
2. **Reviewer registration pre-flight** — `code-quality`/`security` unregistered → stop with
   `REVIEW BLOCKED — required agent not registered: [name]. Manual review required.` Others → warn
   and skip.
3. **Spawn the active reviewers as background sub-agents**, each with `STEERING_ALWAYS` +
   `STEERING_TASK_FORMAT` prepended and the return-small / spill-to-`.dt-review/<agent>.md`
   instruction. Inputs: `diff` (`git diff origin/main`), `task_file`, `code_quality_slice` (empty in
   escape-hatch mode — there is no Phase 1 context packet), `spec_sections` = `$SPEC_SECTIONS`, and
   the config values `$CFG_SMOKE_TEST_MODE`, `$CFG_PROJECT_TYPE`, `$CFG_PROJECT_STACK`,
   `$CFG_CMD_RUN`, `$CFG_REQUIRE_MUTATION_TESTS`, `$CFG_CRITICAL_MODULES`,
   `$CFG_MUTATION_SCORE_THRESHOLD`, `$CFG_SPEC_COVERAGE_ENABLED`, `$CFG_SPEC_COVERAGE_THRESHOLD`,
   plus the worktree path (the feature branch is checked out in the main repo dir for /prepare-pr).
   Reviewers do not read `devteam.config.yml`.
4. **Collect with a per-reviewer deadline** of `$CFG_REVIEW_TIMEOUT_MIN` minutes — a reviewer past
   the deadline is `TaskStop`'d and recorded as `TIMEOUT — no output`.
5. **Build the manifest**, then **run `adversarial` sequentially** (full profile only) with the
   compact manifest.
6. **Assemble the consolidated report + Overall verdict** with the required-agent guard.

Touching protected files or contracts always forces the `full` profile regardless of
`$CFG_REVIEW_PROFILE`.

The reviewers write their full reports under `./.dt-review/` (git-ignored, since /prepare-pr has the
branch checked out in the repo dir rather than a worktree). Read them if you need detail for a fix,
then `rm -rf .dt-review` once the PR is open.

---

## Step 5 — Synthesis

BLOCKER (do not open PR):
- Any test failure not fixed in Step 3
- security: BLOCKER
- smoke-tester: criterion FAIL
- mutation score below threshold (if applicable)

WARNING (open PR with flags):
- security: WARNING
- mutation score below ideal but above minimum

APPROVED: proceed.

If there are BLOCKERs, report and stop:
```
⛔ PR blocked — resolve these before continuing:

[Blocker 1]: [description + file:line]
[Blocker 2]: [description + file:line]

Suggested fixes:
- [fix 1]
- [fix 2]

Fix in the branch and run /prepare-pr T-XXX again
```

---

## Step 5b — Final sync before opening PR

Merges to main may have landed during the review phase. Rebase one last time:
```bash
git fetch origin
git rebase origin/main
```

If there are conflicts:
- Mechanical (whitespace, unrelated imports): resolve alone
- Design (contracts, business logic, schema): stop and present to the user with the same conflict format as Step 2; note that reviewers already ran — flag it so the user can decide whether to re-run them

After a clean rebase, run a quick verify:
```bash
bash scripts/dt-verify.sh
```

If verify fails: report and stop — do not open the PR until clean. The user must fix and re-run `/prepare-pr T-XXX`.

---

## Step 6 — Open PR and update task

Use `$CFG_PR_MODE` (extracted in Step 3b).

If `$CFG_PR_MODE` is `automatic`:

Write the PR body to a temp file, then call `dt-pr.sh`:
```bash
cat > /tmp/pr-body-T-XXX.md <<'EOF'
## Summary
- [what was implemented — bullet 1]
- [what was implemented — bullet 2]
- [what was implemented — bullet 3]

## Acceptance criteria
- [x] criterion 1
- [x] criterion 2

## Review notes
[Code Quality: ...]
[Security: ...]
[Smoke Tests: X/Y criteria PASS]
[Adversarial: found nothing / found X — already fixed]

## Risks
[flagged warnings or "None"]

🤖 Generated with dev-team
EOF

bash scripts/dt-pr.sh T-XXX \
  --title "T-XXX: [task title]" \
  --body-file /tmp/pr-body-T-XXX.md
```
The script creates the PR, captures the URL, moves the task from `tasks/ready-for-pr/` to `tasks/pr-open/`, and commits to main. It outputs `PR_NUMBER`, `PR_URL` and `PR_MERGEABLE`.

If `PR_MERGEABLE=CONFLICTING`, GitHub cannot merge the branch: rebase it on the base branch and force-push, then re-check with `gh pr view $PR_NUMBER --json mergeable`. Report the state to the user either way; `UNKNOWN` just means GitHub had not finished computing.

If `$CFG_PR_MODE` is `manual`:

Print the `gh pr create` command for the user to run:
```bash
gh pr create \
  --title "T-XXX: [task title]" \
  --body-file /tmp/pr-body-T-XXX.md \
  --head "feature/T-XXX-[slug]" \
  --base main
```
Wait for the user to provide the PR URL. Then:
```bash
bash scripts/dt-pr.sh T-XXX --pr-url "[URL provided by user]"
```

---

## Step 7 — Human reviewer summary

```
## PR Ready for Review — T-XXX

What to review:
[2-3 specific points that deserve human attention]

Acceptance criteria: [X/X passed]
Security: [clean / warnings: ...]
Adversarial: [no findings / fixed: ...]

To merge: approve on GitHub and run /done T-XXX
```

---

## Rules

- Never mark the task DONE — that is /done's job
- Never fix behavioral failures automatically — report and stop
- Never open a PR with an unresolved BLOCKER
- Diffs touching protected files or contracts always force full review
