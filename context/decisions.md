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

## 2026-08-04 — T-038 [frontend-agent]
Decided: npm as the package manager for `frontend/` (package-lock.json committed).
Why: the task's own done-when checklist and frontend-agent.md verification commands
hard-code npm; no repo lockfile convention existed yet for any package manager.
Affects: frontend/package-lock.json, all `npm run …` scripts.
Discarded: pnpm/yarn — no prior repo convention to justify the switch.

## 2026-08-04 — T-038 [frontend-agent]
Decided: Vitest + React Testing Library as the frontend test stack.
Why: native Vite integration (shares vite.config.ts), standard for testing by
accessible role rather than implementation detail; design.md's testing strategy
section is Python-only and silent on frontend, so this sets the convention for
future frontend tasks.
Affects: frontend/vite.config.ts (test block), frontend/src/test/setup.ts,
frontend/src/components/Layout.test.tsx, package.json devDependencies.
Discarded: Jest — would need extra config to work with Vite's ESM/TS pipeline
that Vitest gets for free.

## 2026-08-04 — T-038 [frontend-agent]
Decided: Flat ESLint config format (`eslint.config.js`) with typescript-eslint +
eslint-plugin-react-hooks + eslint-plugin-react-refresh + eslint-config-prettier.
Why: current Vite React-TS template default; no prior repo convention to follow.
Affects: frontend/eslint.config.js, package.json devDependencies.
Discarded: legacy `.eslintrc` format — deprecated upstream, not worth adopting new.

## 2026-08-04 — T-038 [frontend-agent]
Decided: No MSW / no runtime mock-data mode in this task.
Why: "mockable for standalone dev" is satisfied via injectable fetch/EventSource/
WebSocket parameters in `frontend/src/api/client.ts` (tests can pass stubs without
a mocking framework), not a runtime mock server — avoids over-building an M-sized
scaffold task.
Affects: frontend/src/api/client.ts.
Discarded: MSW dependency — deferred as a possible future task if standalone-dev
mocking is later needed.

## 2026-08-04 — T-038 [frontend-agent]
Decided: `Sidebar` is left as a static, non-functional placeholder (not wired to
`listRuns()`).
Why: design.md's frontend component table doesn't list a Sidebar/RunList component
and no task explicitly owns wiring it to the API yet; keeps T-038 scoped to "layout
shell, no functionality" as stated in the task's Delivers section.
Affects: frontend/src/components/Sidebar.tsx.
Discarded: wiring it to `listRuns()` now — would pull live-data concerns into a
scaffold-only task.

## 2026-08-04 — T-038 [frontend-agent]
Decided: `ChatConnection` (returned by `connectChat` in `frontend/src/api/client.ts`)
exposes `onError`/`onClose` callbacks, symmetric with the SSE side's `RunEventHandlers.onError`.
Both `onmessage` handlers (SSE and WS) now wrap `JSON.parse` in try/catch and route
parse failures through the relevant `onError` instead of throwing uncaught inside the
event callback.
Why: found during adversarial review — a dropped chat connection or malformed message
was previously silently swallowed with no way for a future `Chat` component to detect
it; `onError`'s type was widened to `Event | Error` so both a native socket error and a
caught parse `Error` can flow through the same callback.
Affects: frontend/src/api/client.ts (`ChatConnection`, `RunEventHandlers`,
`subscribeToRunEvents`, `connectChat`), frontend/src/api/client.test.ts.
Discarded: adding reconnect logic — still explicitly out of scope for this task
(future task's job), `onClose`/`onError` only surface the event.

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

## 2026-08-04 — T-005 [infra-agent]
Decided: `_resolve` rejects `..`-traversal and absolute paths syntactically (component-wise
check on `Path.parts` / `Path.is_absolute()`) but does not resolve symlinks or verify the
final path stays under `workspace_path` on disk.
Why: the task's Done-when criterion is "a path containing `..` raises ValueError" — a syntactic
check on untrusted relative-path arguments. Following it with `.resolve()` + prefix comparison
would additionally guard against symlink escapes already inside a trusted workspace tree, which
is a different threat model not covered by any Done-when item, and risks changing error semantics
in ways untested by this task's suite.
Affects: src/workspace/workspace_manager.py
Discarded: `.resolve()` + `is_relative_to()` prefix check — deferred as a possible hardening if a
future task's threat model requires defending against symlinks planted inside the workspace.

## 2026-08-04 — T-005 [infra-agent]
Decided: `experiment_dir(exp_id)` returns the path without creating the directory; `ensure_dir`
is the only method that creates directories on demand.
Why: design.md's `LabState` stores file *pointers*, not existence guarantees — keeping
`experiment_dir` a side-effect-free path getter avoids a "getter"-named method silently mutating
the filesystem. Callers needing the directory to exist call `ensure_dir(...)` explicitly, or rely
on `write_json`/`write_text`/`write_notebook`'s own parent-dir auto-creation.
Affects: src/workspace/workspace_manager.py
Discarded: having `experiment_dir` also create the directory — would make it behaviorally
identical to `ensure_dir` with a hardcoded `experiments/` prefix, removing the useful distinction
between "compute a path" and "guarantee a path exists".

## 2026-08-04 — T-005 [infra-agent]
Decided: `experiment_dir` does not reject an absolute-looking `exp_id` (e.g. `"/etc/passwd"`)
with `ValueError`, unlike every other method. It builds `f"experiments/{exp_id}"` before
resolving; `Path("experiments//etc/passwd")` normalizes to the *relative* path
`experiments/etc/passwd` rather than an absolute one, so `_resolve`'s `is_absolute()` check
never trips. `..`-traversal in `exp_id` still correctly raises, since `..` survives the string
concatenation as its own path component.
Why: the approved implementation plan's code (verbatim, not to be relitigated) prepends the
`"experiments/"` prefix inside `experiment_dir` itself; the plan's own test list marked
`experiment_dir`'s absolute-path case "ideally" (optional) for exactly this reason. Rather than
assert a false ValueError in the test suite, the test
(`test_absolute_path_raises_value_error`) excludes `experiment_dir` and a dedicated test
(`test_experiment_dir_absolute_looking_exp_id_is_not_rejected`) documents the actual behavior.
Affects: src/workspace/workspace_manager.py, tests/workspace/test_workspace_manager.py
Discarded: changing `experiment_dir` to check `exp_id` for `is_absolute()` before prefixing —
would deviate from the approved class design's literal code without sign-off; noted here instead
as a candidate follow-up if a future task's threat model requires exp_id-level absolute-path
rejection.

## 2026-08-04 — T-005 [infra-agent]
Decided: `write_json`/`write_text`/`write_notebook` write to a sibling `.{name}.{uuid}.tmp` file
in the same directory, then `os.replace(tmp_path, path)` (atomic on POSIX) into place. On any
exception during the write, `_atomic_write` unlinks the temp file (`missing_ok=True`, since some
failure modes never create it) and re-raises, leaving the original file untouched. The except
clause catches `BaseException`, not `Exception`, so cleanup also runs on `KeyboardInterrupt` /
`SystemExit` mid-write, not just ordinary errors.
Why: adversarial review found the original truncating-open-in-place writes corrupted the target
file on a mid-serialization failure (e.g. a non-JSON-serializable value later in the dict left a
partially-written file), and caused concurrent readers to observe truncated/empty files — up to
85%+ `JSONDecodeError` under the reviewer's concurrent read/write repro. design.md allows up to 2
LLM agents to run concurrently, so concurrent access to the same relative path is a realistic
scenario, not a hypothetical.
Affects: src/workspace/workspace_manager.py (`_atomic_write`, `write_json`, `write_text`,
`write_notebook`), tests/workspace/test_workspace_manager.py.
Discarded: file locking (`fcntl`/`portalocker`) — solves a different problem (serializing
concurrent writers) and adds a new dependency; atomic rename alone is sufficient to guarantee
readers only ever see a fully-written file, which was the actual failure mode reported.

## 2026-08-04 — T-005 [infra-agent]
Decided: `_resolve` now also rejects an empty string and `"."` with `ValueError`, in addition to
absolute paths and `..` traversal.
Why: adversarial review found `_resolve("")` passed both existing checks and returned
`self.workspace_path` itself, so every read/write method leaked a raw `IsADirectoryError` instead
of the class's own `ValueError` — an inconsistent error contract for a de facto "point at the
workspace root" input that should never be a valid relative path.
Affects: src/workspace/workspace_manager.py (`_resolve`), tests/workspace/test_workspace_manager.py.
Discarded: leaving `experiment_dir`'s empty/`.` `exp_id` unaddressed — it prefixes with
`"experiments/"` before resolving, so an empty/`.` `exp_id` normalizes to the harmless
`experiments` directory itself, never the workspace root; there was no bug there to fix.

## 2026-08-04 — T-005 [infra-agent]
Decided: `write_notebook` now validates each `cell` is a `dict` and each `source` is a `str`
before touching `nbformat`, raising `ValueError` in both cases; the "unsupported cell_type" error
message now names the valid options.
Why: adversarial review found malformed `cells` input leaked raw, non-`ValueError` exception
types past the class's documented error contract: a non-dict cell raised `AttributeError` from
`.get()`, and a non-string `source` sailed through into `nbformat.validate()`, which raises
`nbformat.reader.NotebookValidationError` — not a `ValueError` subclass — deep inside a
third-party library instead of at the API boundary.
Affects: src/workspace/workspace_manager.py (`write_notebook`), tests/workspace/test_workspace_manager.py.
Discarded: catching `NotebookValidationError` and re-raising as `ValueError` around the
`nbformat.validate()` call instead — validating our own input shape earlier gives a clearer error
message pointing at the actual malformed cell, rather than a generic schema-validation failure.

## 2026-08-04 — T-002 [infra-agent]
Decided: `new_state()` defaults `max_iterations=10` and `phase=""`.
Why: neither default is specified by design.md's `LabState` TypedDict block itself.
`max_iterations=10` is sourced from design.md's documented `execution.max_iterations: 10`
setting (the `config/settings.yaml` this maps to doesn't exist as a file yet). `phase=""` is
used instead of hardcoding a starting pipeline phase name, since phase-name conventions belong
to pipeline-agent's `src/graph/` work, not this protected `src/state.py` contract.
Affects: src/state.py (`new_state` defaults)
Discarded: hardcoding a phase name like `"understanding"` — would bake a pipeline-agent naming
convention into a protected infra-agent contract, creating a cross-module coupling this task
has no visibility to keep in sync.

## 2026-08-04 — T-003 [infra-agent]
Decided: `CriticConfig` shape is `{node: str, targets: list[str], max_retries: int}`.
Why: design.md's `PhaseConfig` dataclass references `critic: CriticConfig | None` but never
defines the type itself; the shape is inferred from design.md's `config/phases/phase1_understanding.yaml`
example (`critic: {node: analysis_critic, targets: [...], max_retries: 3}`), which is the only
concrete source of truth available.
Affects: src/config/schema.py.
Discarded: nothing — no alternative shape was suggested anywhere in design.md. Flagging for
confirmation before T-009 (GraphBuilder) consumes this dataclass, in case the real usage needs
additional fields.

## 2026-08-04 — T-003 [infra-agent]
Decided: `ModelsConfig` is a fixed-attribute dataclass (`advisor`, `reasoning`, `implementation`,
`research`, `fast` as named fields), not `dict[str, ModelRoleConfig]`.
Why: so `settings.models.reasoning.provider` — the task's literal acceptance-criterion expression
— works via plain attribute access.
Affects: src/config/settings.py.
Discarded: dict-keyed `models: dict[str, ModelRoleConfig]` — would require `settings.models["reasoning"]`
instead, not matching the stated acceptance test.

## 2026-08-04 — T-003 [infra-agent]
Decided: `${ENV_VAR}` resolution walks the *parsed* YAML dict/list/str tree (post `yaml.safe_load`),
not the raw YAML text via regex.
Why: keeps the substitution regex scoped to plain Python string values and avoids any risk of it
interfering with YAML syntax (quoting, block scalars, etc.) if it ran over raw text instead.
Affects: src/config/settings.py (`_resolve_env_vars`).
Discarded: raw-text regex substitution before parsing — simpler at first glance but couples env
resolution to YAML's own escaping rules for no benefit.

## 2026-08-04 — T-003 [infra-agent]
Decided: `ConfigError` message convention, used consistently in `src/config/settings.py` and
`src/config/loaders.py`:
- Missing env var: `"Missing required environment variable '{VAR}' referenced in {source}"`
- Missing/invalid required field: `"Missing required field '{field}' in {file_path}"`
Why: gives every config failure a predictable, greppable message shape naming both the offending
key and the file/source it came from, instead of ad hoc wording per call site.
Affects: src/config/settings.py, src/config/loaders.py.
Discarded: letting raw `KeyError`/`TypeError` propagate from dataclass construction — would not
name the source file and would break the task's literal acceptance criterion ("missing env var
raises `ConfigError` naming the missing key").

## 2026-08-04 — T-003 [infra-agent]
Decided: `load_agent_config`/`load_phase_config` take an optional `base_dir: str | Path | None = None`
param, defaulting to the real `AGENTS_DIR`/`PHASES_DIR` (`config/agents/`, `config/phases/`) when
omitted.
Why: those directories don't exist on disk yet — they're first populated by later pipeline-agent
tasks (T-013+) — so tests need a way to point the loader at `tests/fixtures/config/{agents,phases}/`
without touching production code paths. Production callers use the 1-arg form, matching
design.md's literal `load_agent_config(name) -> AgentConfig` signature.
Affects: src/config/loaders.py.
Discarded: no test-injection param (would make T-003's own loader tests impossible before those
directories exist); a module-level monkeypatchable constant instead of a function param (more
indirect for the same effect).

## 2026-08-04 — T-003 [infra-agent]
Decided: a missing config file (settings.yaml, agent YAML, phase YAML, prompt file) raises the
stdlib `FileNotFoundError` as-is from `Path.read_text()`, never a wrapped/custom exception.
Why: `FileNotFoundError`'s default message/args already contain the path that was looked up;
wrapping it would just re-derive information Python already gives for free, and the task's own
acceptance criterion for `PromptLoader` explicitly asks for `FileNotFoundError` with the path.
Affects: src/config/settings.py, src/config/loaders.py, src/config/prompts.py.
Discarded: wrapping in `ConfigError` for a uniform exception type across all config failures —
rejected because it would obscure the distinction between "file missing" (an I/O condition) and
"file present but malformed" (a config-content condition), and the task spec is explicit that
missing-file stays `FileNotFoundError`.

## 2026-08-04 — T-003 [infra-agent]
Decided: no caching or singleton for `Settings` in this task — `Settings.load()` re-reads and
re-resolves `config/settings.yaml` from disk on every call.
Why: keeps T-003 scoped to "loader", not "runtime state management"; the task's Delivers list is
the loader, not a shared instance lifecycle.
Affects: src/config/settings.py.
Discarded: module-level cached singleton — left as an open question for whichever later task
(likely T-004 LLMFactory) becomes the actual long-lived consumer; that task should decide whether
to load once at startup and share the instance.

## 2026-08-04 — T-003 [infra-agent]
Decided: `config/agents/`, `config/phases/`, `config/prompts/` directories are not created in this
task (no tracked empty/placeholder dirs under them).
Why: out of T-003's declared Delivers (`settings.yaml`, `Settings` loader, `AgentConfig`/`PhaseConfig`,
`PromptLoader`, `load_agent_config`/`load_phase_config`) — the first task that adds a real
agent/phase/prompt file (T-009, T-013+) creates its own parent directory implicitly by writing
into it.
Affects: config/ (directory layout).
Discarded: pre-creating empty dirs with `.gitkeep` placeholders — adds tracked no-op files for
directories no code in this task reads from yet.
