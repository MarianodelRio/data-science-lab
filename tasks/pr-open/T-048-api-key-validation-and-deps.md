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

## Completed
- What was implemented: `_missing_api_key_descriptors(raw_api_keys)` (module-level
  helper in `src/config/settings.py`, placed after `_build_model_role_config`) and
  `Settings.validate_required_keys(path: str | Path = SETTINGS_PATH)` (classmethod,
  placed after `load()`). The helper iterates `dataclasses.fields(ApiKeysConfig)`
  and flags a field as failing when its raw value is missing/non-string/empty, or
  references one or more `${ENV_VAR}` placeholders where at least one referenced
  var is unset or empty (using `_ENV_VAR_PATTERN.findall`, so only the actually
  failing vars are named — a literal non-`${}` string counts as valid if
  non-empty). `validate_required_keys()` re-reads and re-parses the YAML file on
  every call (no caching, matching `load()`'s existing behavior), validates only
  the `api_keys` section via `_require_section`, and raises one `ConfigError`
  naming every missing/empty key via the helper. Added 16 tests to
  `tests/unit/config/test_settings.py` (8 classmethod-level, 8 helper-level) and
  a new `tests/unit/test_pyproject_dependencies.py` (2 tests) asserting `torch` is
  a direct dependency with a permissive lower bound. Added
  `"torch>=2.0"` to `[project.dependencies]` in `pyproject.toml`, directly after
  `sentence-transformers`. Appended "API key preflight validation" and
  "Dependencies" sections to `docs/configuration.md`. Full suite (2267 tests),
  `ruff check`/`ruff format --check`, and `mypy src/` all pass.
- Deviations from plan: None. Implementation, tests, and docs follow the
  Planner's plan verbatim, including the exact function bodies and test cases
  specified. Chose `torch>=2.0` as the lower bound after confirming empirically
  (`pip index versions torch --index-url https://download.pytorch.org/whl/cpu`)
  that the CPU wheel index currently resolves up to `2.14.0+cpu` for this
  platform — `>=2.0` is a safe, conservative floor below every version that
  index currently serves.
- Key decisions: `validate_required_keys()` deliberately duplicates three lines
  from `load()` (file read, `yaml.safe_load`, top-level-mapping check) instead of
  calling `load()` internally — calling `load()` would fail on any malformed
  section (defeating the "keys-only preflight, works even if `models`/`context`/
  etc. are broken" property) and would trigger `_resolve_env_vars`'s
  raise-on-first-miss behavior across the whole file instead of aggregating only
  `api_keys` failures. `_ENV_VAR_PATTERN.findall` (not `.search`) mirrors
  `_resolve_env_vars`'s use of `.sub`, which substitutes every match, so a field
  value with more than one `${VAR}` reference is handled consistently with how
  `Settings.load()` would handle it, even though no field in the real
  `config/settings.yaml` currently has more than one. No logging was added
  (`src/config/` has none today, and the natural call site — a future API
  startup hook — is out of scope for T-049, not this task).
- Dependencies added: `torch>=2.0` to `[project.dependencies]` in
  `pyproject.toml` (no `pyproject.toml` version pin change to `tomli`/`tomllib` —
  the version-gated import in the new test file uses `tomli`, already present
  transitively via `coverage`'s `python_version<'3.11'` dependency on Python
  3.10, and stdlib `tomllib` on Python ≥3.11; no new dependency manifest entry
  was needed for it).
