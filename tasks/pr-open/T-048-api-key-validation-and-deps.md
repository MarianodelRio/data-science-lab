---
id: T-048
phase: 6
agent: infra-agent
depends_on: []
status: pr-open
folders: [src/config/, pyproject.toml, docs/configuration.md]
outputs: [Settings.validate_required_keys()]
size: M
branch: feature/T-048-api-key-validation-and-deps
pr: https://github.com/MarianodelRio/data-science-lab/pull/48
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
- `Settings.validate_required_keys()` (classmethod, callable without a prior
  successful `Settings.load()`) that checks all required provider API keys
  (exactly the fields of `ApiKeysConfig`, derived via `dataclasses.fields`,
  never re-derived from `LLMFactory`/`models.*`) are present and non-empty,
  and raises a `ConfigError` naming every missing/empty key at once (not just
  the first) — via a new aggregating helper, since `_resolve_env_vars` raises
  on the first miss and cannot be reused for this
- PyTorch declared as an explicit `pyproject.toml` dependency with a
  permissive lower bound (no exact pin), matching what `Dockerfile.api`
  installs. `Dockerfile.api` itself is untouched: its two-step install
  (CPU-wheel `torch` pre-install, then `pip install .`) is not redundant — it
  exists specifically to avoid pulling PyTorch's default CUDA-bundled wheel
  (~2.5GB heavier)

**Done when:**
- [ ] `Settings.validate_required_keys()` raises `ConfigError` listing all
      missing/empty required keys
- [ ] passes cleanly when all required keys are present
- [ ] `torch` appears in `[project] dependencies` in `pyproject.toml`, and a
      test asserts the project declares it directly rather than only
      inheriting it transitively via `sentence-transformers`
- [ ] `Settings.load()`'s existing behavior and T-003's `ConfigError` message
      conventions are unchanged — existing tests in
      `tests/unit/config/test_settings.py` pass unmodified
- [ ] CI installs cleanly with the new dependency
- [ ] tests written and passing (types per the Testing strategy in `design.md`)
- [ ] `docs/configuration.md` updated
