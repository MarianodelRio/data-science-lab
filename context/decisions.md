# Decisions

Log of technical decisions made during implementation.

## Format

```
## YYYY-MM-DD — T-XXX [Agent name]
Decided: [what]
Why: [reason]
Affects: [files/modules]
Discarded: [alternative and why not]
```

## 2026-08-04 — T-001 [infra-agent]
Decided: `ruff.toml` sets `extend-exclude = ["*.md"]`.
Why: ruff 0.16 formats fenced Python code blocks inside Markdown files by default. This made
`ruff format --check .` (part of devteam.config.yml's `lint` command) fail on pre-existing
`IDEA.md` and `design.md` — files untouched by this task, containing illustrative code snippets
that are documentation, not source. Every future PR's lint gate would show this same unrelated
failure without an exclude.
Affects: ruff.toml (repo-wide lint config)
Discarded: leaving markdown unformatted-but-failing and asking each future task to reformat docs
it doesn't own — would create noisy unrelated diffs and cross-agent scope violations.

## 2026-08-04 — T-001 [infra-agent]
Decided: root `conftest.py` overrides `pytest_sessionfinish` to convert exit code 5
("no tests collected") into exit code 0.
Why: the task's acceptance criterion is "`pytest` exits 0 (no tests collected is OK)", but
pytest's native exit code for zero collected tests is 5, not 0. Needed for `pip install -e ".[dev]"`
→ `pytest` to be scriptable/CI-friendly during the scaffold-only stage before any tests exist.
Affects: conftest.py (repo-wide pytest behavior) — this will keep masking a genuinely empty
`testpaths` in any future PR too, not just this one; a future task with real tests that
accidentally collects zero tests will still exit 0 instead of failing loudly.
Discarded: leaving exit code 5 as a "failure" — would break the task's own literal acceptance
criterion and block early scaffold/infra PRs that legitimately ship no tests yet.
