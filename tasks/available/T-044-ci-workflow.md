---
id: T-044
phase: 5
agent: infra-agent
depends_on: [T-001]
status: available
folders: [".github/", "README.md"]
outputs: [.github/workflows/ci.yml]
size: S
branch: ~
pr: ~
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
