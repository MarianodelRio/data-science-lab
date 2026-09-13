---
id: T-044
phase: 5
agent: infra-agent
depends_on: [T-001]
status: pr-open
folders: [".github/", "README.md"]
outputs: [.github/workflows/ci.yml]
size: S
branch: feature/T-044-ci-workflow
pr: https://github.com/MarianodelRio/data-science-lab/pull/45
---

## CI workflow (.github/workflows/ci.yml)

**Scope:** `.github/workflows/ci.yml`, plus a doc note in `README.md`.

**Delivers:**
- Triggers on `pull_request` → main and `push` → main (same job set on both)
- Jobs: `test` (pytest + coverage — no Chroma service container; `tests/tools/test_rag.py` runs against `chromadb.EphemeralClient()` in-memory, and no test in the repo dials a real Chroma server today), `lint` (ruff), `type_check` (mypy); a `frontend` job (npm ci + lint + build)
- Python job commands sourced from `devteam.config.yml` (`commands.test`/`lint`/`type_check`); frontend job commands from `frontend/package.json` scripts
- Coverage gate at the configured threshold (`devteam.config.yml` → `commands.test`'s `--cov-fail-under`)
- Node job pinned to `>=20.19` per `frontend/package.json` `engines` (open discovery: T-038 → infra-agent)
- Cache `~/.cache/huggingface` (sentence-transformers pulls `all-MiniLM-L6-v2` on a cold runner — open discoveries: B-001 review and T-008, both → CI/T-044) and pip, to bound job time
- Follow-up task (spin off separately, do not implement here): real-Chroma integration tests for `src/tools/rag`, requiring a `chroma_host`/`chroma_port` env override in `src/config/` — `config/settings.yaml` is a protected contract and out of scope for this S task

**Done when:**
- [ ] workflow YAML is valid (parses; `actionlint` clean if available)
- [ ] both `pull_request` and `push` to main run the identical job set
- [ ] the frontend job runs `npm ci && npm run lint && npm run build`
- [ ] coverage failure below threshold fails the job (verify empirically against `conftest.py`'s `pytest_sessionfinish` exit-5→0 override — T-001 decision — to confirm a genuine coverage failure still fails the job)
- [ ] Node job pinned to `>=20.19`
- [ ] HF cache and pip cache steps present
- [ ] `README.md`'s existing `## Docker / CI` section gains a `### CI` subsection describing the workflow (no new `CONTRIBUTING.md`)

## Completed
- Added `.github/workflows/ci.yml` with a single `"on":` block (`pull_request` and `push`, both scoped to `branches: [main]`) feeding one `jobs:` map, so the identical job set runs on both triggers. Four jobs: `test` (`pytest --cov=src --cov-fail-under=70 -x`, plus an `actions/cache@v4` step for `~/.cache/huggingface` keyed on `${{ runner.os }}-hf-all-MiniLM-L6-v2` with a `restore-keys` fallback), `lint` (`ruff check . && ruff format --check .`), `type_check` (`mypy src/`) — all three Python jobs install via `pip install -e ".[dev]"` and use `setup-python`'s built-in `cache: "pip"`; and `frontend` (`npm ci && npm run lint && npm run build` in `frontend/`, `setup-node` pinned to `"20.19"` per `engines: ">=20.19.0"`, built-in `cache: "npm"` keyed off `frontend/package-lock.json`). All Python/frontend commands are copied verbatim from `devteam.config.yml`'s `commands.test`/`lint`/`type_check` and `frontend/package.json`'s scripts, per the plan.
- Added `tests/unit/test_ci_workflow.py` (6 tests) asserting: the workflow file exists, it parses as non-empty YAML, `on.pull_request`/`on.push` both target `branches: ["main"]`, the job set is exactly `{test, lint, type_check, frontend}`, the frontend job's concatenated `run:` steps contain `npm ci`/`npm run lint`/`npm run build`, and the three Python jobs' `run:` steps contain `devteam.config.yml`'s `commands.test`/`lint`/`type_check` values read live from that file (not hardcoded a second time) so the test won't silently drift if the config changes.
- Added a `### CI` subsection to `README.md` immediately after the existing `### Verify` subsection under `## Docker / CI`, describing the four jobs, the coverage gate, the no-secrets-needed design (tests inject dummy values via `monkeypatch`), and the HF cache. No other README subsection was touched; no `CONTRIBUTING.md` was created.
- Empirically verified the coverage gate is not swallowed by `conftest.py`'s `pytest_sessionfinish` exit-5→0 override: `pytest --cov=src --cov-fail-under=70 -x` → exit 0 (2248 passed, 97.26% coverage); `pytest --cov=src --cov-fail-under=100 -x` → exit 1 (`FAIL Required test coverage of 100% not reached`). The override is scoped to exitstatus 5 ("no tests collected") only, so a genuine `--cov-fail-under` failure (exit 1) still fails the job, confirming the Planner's static-read conclusion.
- `actionlint` is not installed in this environment (`which actionlint` → exit 1, matching the Planner's finding), so the "actionlint clean if available" Done-when item is satisfied by the "if available" clause — the YAML-parse test in `tests/unit/test_ci_workflow.py` is the enforced minimum bar, as the plan specifies.
- Verified `ruff check . && ruff format --check .` (all checks passed, 166 files already formatted), `mypy src/` (no issues, 90 source files), and `pytest --cov=src --cov-fail-under=70 -x` (2248 passed, including the 6 new tests, 97.26% coverage) all pass in the worktree with the new files present.

Deviations from plan: None — implemented exactly as specified, including the `"on":` quoting to avoid the YAML 1.1 boolean-key footgun.

Key decisions: None beyond what the plan already specified. No secrets/env vars were added, matching the plan's verified conclusion that no test path in the repo reads real env vars without `monkeypatch`; no discovery was filed since nothing found during implementation affected a module outside `folders: [".github/", "README.md"]`.

Dependencies added: None.
