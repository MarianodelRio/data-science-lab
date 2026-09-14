---
id: T-048
phase: 6
agent: infra-agent
depends_on: []
status: available
folders: [src/config/, pyproject.toml]
outputs: [Settings.validate_required_keys()]
size: M
branch: ~
pr: ~
---

## Startup hardening: API key preflight validation + align dependencies with Docker

**Scope:** `src/config/` and `pyproject.toml`. Two related pieces of pre-flight
hardening bundled together: (1) the app can boot today with empty provider API
keys and only fails once the pipeline tries to call that provider mid-run; (2)
`Dockerfile.api` installs PyTorch but `pyproject.toml` does not declare it, so a
local `pip install -e ".[dev]"` does not match what Docker actually ships, and
`deep_learning_specialist`-generated code can silently `ImportError` outside
the container.

**Delivers:**
- `Settings.validate_required_keys()` (or equivalent) that checks all required
  provider API keys are present and non-empty, and raises a `ConfigError`
  naming every missing key at once (not just the first)
- PyTorch declared as an explicit `pyproject.toml` dependency, matching what
  `Dockerfile.api` installs; simplify `Dockerfile.api`'s install step if it
  becomes redundant

**Done when:**
- [ ] `Settings.validate_required_keys()` raises `ConfigError` listing all
      missing/empty required keys
- [ ] passes cleanly when all required keys are present
- [ ] `pip install -e ".[dev]"` provides `torch` without relying on the Docker
      image
- [ ] CI installs cleanly with the new dependency
- [ ] tests written and passing (types per the Testing strategy in `design.md`)
- [ ] `docs/configuration.md` updated
