---
id: T-044
phase: 5
agent: infra-agent
depends_on: [T-001]
status: blocked
folders: [".github/"]
outputs: [.github/workflows/ci.yml]
size: S
branch: ~
pr: ~
---

## CI workflow (.github/workflows/ci.yml)

**Scope:** `.github/workflows/ci.yml`.

**Delivers:**
- Triggers on `pull_request` → main and `push` → main (same job set on both)
- Jobs: `test` (pytest + coverage, spins a Chroma service container for integration tests), `lint` (ruff), `type_check` (mypy); a `frontend` job (npm ci + lint + build)
- Commands sourced from `devteam.config.yml`
- Coverage gate at the configured threshold

**Done when:**
- [ ] workflow YAML is valid (parses; `actionlint` clean if available)
- [ ] both `pull_request` and `push` to main run the identical job set
- [ ] the test job provisions a Chroma service for integration tests
- [ ] the frontend job runs `npm ci && npm run lint && npm run build`
- [ ] coverage failure below threshold fails the job
- [ ] `docs/` CONTRIBUTING/CI note updated
