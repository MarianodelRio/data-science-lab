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

## 2026-08-04 — T-003 [infra-agent]
Decided: `CriticConfig.targets`, `AgentConfig.tools`, and `PhaseConfig.nodes`/`sequence`/
`parallel_nodes` are `tuple[str, ...]`, not `list[str]` as originally specced. `loaders.py`
coerces the parsed YAML lists into tuples when constructing these dataclasses.
Why: post-review hardening (protected-contract shape fix, before T-004/T-009/T-010/T-011 start
importing the shape as-is). `list` isn't hashable, so `@dataclass(frozen=True)` instances
containing a list field are themselves unhashable despite `frozen=True` — inconsistent with the
"frozen means immutable and hashable" contract. `list` is also only shallowly immutable: a caller
holding a reference to `agent_config.tools` could mutate it in place, silently corrupting the
frozen dataclass from outside. Tuples close both gaps.
Affects: src/config/schema.py, src/config/loaders.py, tests/unit/config/test_loaders.py.
Discarded: leaving list fields as originally specced and documenting "don't mutate" as a
convention — unenforceable, and defeats the purpose of `frozen=True` in the first place.

## 2026-08-05 — T-006 [infra-agent]
Decided: `code_executor.execute()` builds the child subprocess environment from an explicit
allow-list (`PATH`, `HOME`, `LANG`, `LC_ALL`, `VIRTUAL_ENV`, `PYTHONPATH`) via `_build_child_env()`,
rather than inheriting the orchestrator's full `os.environ`.
Why: adversarial security review found that inheriting `os.environ` wholesale would leak
`ANTHROPIC_API_KEY`/`DEEPSEEK_API_KEY`/`GROQ_API_KEY`/Kaggle credentials into any subprocess
running LLM-generated code, and since `ExecResult.stdout`/`stderr` get persisted to
`runs/{run_id}/execution.jsonl` (design.md § Observability) and feed the RAG store, an accidental
or prompt-injected `os.environ` dump would become a durable secret leak.
Affects: src/tools/code_executor.py.
Discarded: inheriting `os.environ` and relying on generated code never printing it — not
enforceable against LLM-generated/prompt-injected code, which is the actual threat model here.

## 2026-08-05 — T-006 [infra-agent]
Decided: the post-`killpg` `communicate()` call is bounded by its own short timeout
(`_POST_KILL_GRACE_SECONDS = 5`); on a second `TimeoutExpired` the pipes are closed directly and
best-effort partial output is returned instead of blocking further.
Why: adversarial review found that if executed code spawns its own `start_new_session=True`
grandchild process, that grandchild survives `os.killpg` (different process group) but still holds
the stdout/stderr pipe fds open, so an un-timed second `communicate()` would block forever waiting
for all writers to close — silently hanging the whole pipeline despite a configured timeout.
Affects: src/tools/code_executor.py.
Discarded: relying on `killpg` alone to guarantee pipe closure — verified false for any
LLM-generated code that itself spawns a detached subprocess, which is realistic given this tool
runs arbitrary ML training code.

## 2026-08-05 — T-004 [infra-agent]
Decided: `LLMFactory` caches `Settings` in a class-level `ClassVar[Settings | None]`, populated by
calling `Settings.load()` exactly once — on the first `LLMFactory.get(role)` call in the process —
and reused by every subsequent `.get()` call. Role resolution (`role: str` → `ModelRoleConfig`)
and provider dispatch (`role_config.provider` → LangChain chat model class) both go through
explicit `dict[str, Callable[...]]` mappings rather than `getattr`/if-elif chains: an unrecognized
role raises `KeyError(role)`, an unrecognized provider raises
`ValueError(f"Unknown provider: {provider!r}")`.
Why: T-003 explicitly left the caching decision open for T-004 as the actual long-lived consumer
(see the 2026-08-04 T-003 entry above) — `Settings.models` being a fixed-attribute frozen
dataclass (not dict-keyed) means a bare `getattr(settings.models, role)` would raise
`AttributeError` on a bad role, which fails the task's Done-when criterion requiring `KeyError`;
an explicit mapping makes that guarantee mechanical rather than relying on `hasattr` guards
scattered through the dispatch code. The same reasoning applies to provider dispatch: an
if-elif chain risks a silent fallthrough or the wrong exception type as providers are added.
Affects: src/llm/factory.py.
Discarded: reloading `Settings` on every `.get()` call (matches `Settings.load()`'s own
no-caching behavior, but reintroduces file I/O and env-var resolution on every LLM instantiation
across ~25 agents, and defeats the "process needs a restart to pick up a model change" contract
already documented in docs/configuration.md § Changing a model); `getattr`-based role resolution
(fails the KeyError requirement, see above).

## 2026-08-05 — T-009 [pipeline-agent]
Decided: `analysis_critic`, `code_critic`, and `specialist_selector` own their control flow
internally instead of the graph exposing critic-retry or specialist-dispatch as conditional
edges. A critic's own node function re-invokes its target node(s) directly (via the same
`resolve_node` mechanism `GraphBuilder` uses) up to `max_retries`, entirely inside its own
`__call__`/`run`, never surfacing as a graph-level conditional edge; `specialist_selector`
internally invokes exactly one chosen specialist the same way. Applied consistently to both.
Why: the Planner flagged that critics/`specialist_selector` need verdict/retry-count/selected-
specialist state to drive branching, but `LabState` has none of those fields, and adding one is a
protected-contract change requiring separate human approval that this task does not have. Keeping
the control flow inside the owning node's own function sidesteps the need for new `LabState`
fields entirely — the node can loop/dispatch using purely local variables — while leaving the door
open for a future task to promote this to real conditional edges if a shared-state need for
retry/verdict visibility emerges later.
Affects: `config/phases/phase1_understanding.yaml`, `phase4_design.yaml`,
`phase5_implementation.yaml` (`sequence` stays a flat one-pass list — critics/specialists are
listed once each, not branched); `src/graph/builder.py` and `src/graph/supervisor.py` (no
critic-retry or specialist-dispatch branching logic — the graph wires phases sequentially per
`sequence`, full stop). Does not affect any critic/specialist node implementation itself — those
are future tasks (T-016, T-023, T-030), not built by T-009.
Discarded: adding `critic_verdict`/`retry_count`/`selected_specialist` fields to `LabState` now to
support graph-level conditional edges for retry/dispatch — would be the more "idiomatic" LangGraph
pattern, but requires modifying a protected contract (`src/state.py`) without approval; deferred
to whichever future task actually needs cross-node visibility into that state.

## 2026-08-05 — T-008 [infra-agent]
Decided: `IndexDocument` (`src/memory/store.py`) is the frozen dataclass contract for
everything indexed into the RAG store: id: str (default uuid4), text: str, source: str,
problem_type: list[str], methods_used: list[str], dataset_characteristics: list[str],
key_findings: str, relevance_score: float.
`RagStore` (src/tools/rag.py) never extracts these fields itself — no LLM import anywhere in
src/tools/rag.py or src/memory/store.py. Callers (T-017 literature_researcher and other
research/memory nodes) construct IndexDocument objects via their own LLM extraction step and
pass them to RagStore.index(); RagStore is pure storage/embedding/retrieval.
Why: design.md classifies `rag` as a Tool (not one of the 21 LLM nodes) and CLAUDE.md
invariant #8 forbids compute/tool modules from importing an LLM module.
Affects: src/memory/store.py, src/tools/rag.py, and every future node constructing
IndexDocument (T-017, T-018, T-019, T-021, T-032 per T-008's task file).
Discarded: extracting metadata inside RagStore.index() (design.md's "Indexing pipeline" step
2 reads as if extraction happens as part of indexing) — rejected since it would make
src/tools/rag.py an LLM-calling module, violating invariant #8.

## 2026-08-05 — T-008 [infra-agent]
Decided: `RagStore.query(where=...)` accepts design.md's literal
`{"problem_type": {"$in": [...]}}` shape (also for `methods_used`,
`dataset_characteristics`) and translates it internally into Chroma's `$or`-of-`$contains`
before calling the collection — Chroma (chromadb==1.5.9) does not match `$in` against
list-valued metadata directly (verified empirically), only against scalar metadata fields.
Why: problem_type/methods_used/dataset_characteristics are list[str] per the IndexDocument
schema (a document can belong to multiple problem types), but design.md's own retrieval
example and this task's done-when checklist both hard-code the `$in` call shape —
translating at the RagStore boundary satisfies both without weakening the schema to
single-valued scalars.
Affects: src/memory/store.py (translate_where, LIST_VALUED_METADATA_FIELDS),
src/tools/rag.py (RagStore.query).
Discarded: storing problem_type etc. as a single scalar "primary" value — would silently
drop information for multi-label documents and contradicts the list[str] schema.

## 2026-08-05 — T-008 [infra-agent] (correction, review round 1)
Decided: `sanitize_collection_name` (`src/memory/store.py`) is corrected from pure
lossy-character-sanitization to `rag_{readable}_{digest}`, where `digest` is a 16-hex-char
`sha256(competition_name)` prefix and is the sole source of uniqueness; `readable` is now
purely a cosmetic debug aid with no uniqueness guarantee.
Why: code review (independently reproduced against a live `chromadb.EphemeralClient()`) found
the original char-replace-then-strip scheme collided on distinct inputs — `"foo bar"` vs.
`"foo_bar"` (space vs. literal `_`) and `"comp!"` vs. `"comp"` (trailing-char stripping after
replacement) both sanitized to the identical collection name. Two different competitions could
therefore silently share one Chroma collection, making one competition's indexed RAG documents
retrievable by another — a real cross-tenant data leak, not a cosmetic naming edge case. This
supersedes the original "collision-free via character clamping" assumption implicit in the
first-pass `sanitize_collection_name` (not separately logged as a decision at the time).
Affects: src/memory/store.py (sanitize_collection_name); every existing/future
`RagStore(competition_name=...)` caller — collection names change shape but the public
`RagStore`/`IndexDocument` API is unaffected.
Discarded: tightening the character-replacement/stripping rules further to try to make
sanitization itself injective — rejected because any purely lossy text transform is
fundamentally not guaranteed collision-free against arbitrary input; hashing the raw input is
the only approach that actually guarantees it.

## 2026-08-06 — [Orchestrator, /explore]
Decided: all 3 human checkpoints (`phase1_understanding`, `phase4_design`,
`phase6_evaluation`) are forward-only. No interrupt can trigger re-execution of a phase that
already completed; `human_feedback` is read-only advisory context for future nodes, never a
field that changes graph routing. `supervisor`'s decision at the `phase6_evaluation`
checkpoint stays based exclusively on `iterations_without_improvement` vs `max_iterations` —
it does not read `human_feedback` — making it just as informational as the checkpoints after
phase1/phase4. No `LabState` changes.
Why: keep the model simple for now; this can be revisited later if a real "undo" is needed
(would require a verdict field on `LabState`, a protected-contract change not approved today).
Affects: `T-036` (explainer), `T-041` (chat frontend), `T-034`/`T-037` (resume endpoints), any
future node reading `state["human_feedback"]`. Does not affect `src/graph/` — the code
(`supervisor.py`, `builder.py`) already implements this behavior; this decision only aligns
`design.md`/`docs/pipeline.md`/`IDEA.md`/task-file wording that previously implied otherwise.
Discarded: letting the phase6 checkpoint override `supervisor`'s routing — would require a new
`LabState` field, out of scope now.

## 2026-08-06 — T-012 [infra-agent] (correction, review round 1)
Decided: flagging a latent, unfixed security concern surfaced during T-012 review rather than
addressing it now (documentation only, no code change).
`JsonlCallbackHandler._summarize_output` (`src/observability/jsonl_callback.py`) writes the
last LLM message's `content` into `execution.jsonl`'s `output_summary` field near-verbatim —
200-char truncated, whitespace-collapsed, but not redacted in any way. Today every message
flowing through `LabState.messages` originates from this codebase's own prompts/LLM responses,
so this is low-risk. Once a future task feeds tool/subprocess stdout (e.g. a failed code
execution's traceback, a shell command's output) through `LabState.messages` for LLM
self-correction — as `design.md`'s Implementation/Evaluation phases are expected to eventually
need — `output_summary` becomes an unguarded potential secret-leak sink: any credential,
token, or sensitive path that ends up in that stdout would be copied into the JSONL log
verbatim.
Why: out of T-012's `src/observability/`-only scope to design a redaction scheme now (no
current caller produces sensitive content in `messages`); recording it here so whichever task
wires tool/subprocess output into `LabState.messages` revisits `_summarize_output` at that
point rather than shipping the leak silently.
Affects: `src/observability/jsonl_callback.py` (`_summarize_output`); any future task wiring
tool/subprocess stdout into `LabState.messages` for LLM self-correction.
Discarded: adding redaction/scrubbing logic to `_summarize_output` now — no real sensitive-data
path exists yet to test against, and guessing at a redaction scheme without a concrete threat
model risks both false confidence and missed patterns.

## 2026-08-07 — T-016 [pipeline-agent] (correction, review round 1)
Decided: `analysis_critic`'s phase-identity detection (`_detect_phase_stem`,
`src/nodes/llm/analysis_critic.py`) reads `state["feature_spec_path"]` as a proxy for "am I
reviewing Phase 4 (Design) or Phase 1 (Understanding)", never `state["phase"]` — `state["phase"]`
is only stamped by the graph *after* a phase subgraph finishes, so while `analysis_critic` itself
is running (as the last node inside the very phase it reviews) that field still names the
*previous* completed phase, not the current one. No code change from this entry — the heuristic
already shipped this way in T-016's initial implementation; this entry documents it explicitly per
adversarial review.
Why flagging now: this proxy is sound only because `feature_spec_path` is monotonic — set once by
`feature_engineer` in Phase 4 and never reset afterward within a given `LabState` — combined with
the 2026-08-06 `[Orchestrator, /explore]` entry above establishing that all 3 human checkpoints are
currently forward-only (no interrupt can re-trigger a completed phase). If a future task revisits
that forward-only decision to support resuming/re-executing an *earlier* phase (e.g. Phase 1) after
a *later* one (Phase 4) has already run in the same `LabState` — checkpoint "time travel" — this
heuristic would misdetect Phase 1 as Phase 4 (since `feature_spec_path` would already be non-empty
from the earlier Phase 4 pass). Not reachable today; flagged so whoever revisits forward-only
checkpoints also revisits this heuristic.
Affects: `src/nodes/llm/analysis_critic.py` (`_detect_phase_stem`). Related to the 2026-08-06
`[Orchestrator, /explore]` entry's checkpoint forward-only decision above.
Discarded: nothing — no functional change, documentation only.

## 2026-08-07 — T-017 [pipeline-agent]
Decided: `build_index_documents` sorts its returned `IndexDocument` list by the extraction
entry's validated 1-based `index` before returning, rather than preserving the LLM's raw
JSON-array order. The plan didn't specify an output order; sorting makes the result
deterministic and guarantees positional alignment with the original `sources` list (`documents[i]`
corresponds to `sources[i]`), which `render_report_markdown` relies on via `zip(sources,
documents, strict=True)`.

Decided: `literature_researcher.py` and `web_researcher.py` duplicate a larger surface than just
query-building — `__init__`, `_ensure_client`, `_ensure_rag_store`, `_build_query`,
`_read_problem_type`, `_build_messages`, and `_write_output` are all identical or near-identical
(the only real differences are which production `SearchClient` class each defaults to, and the
report title/`output_file_pattern` string). None of this was hoisted into `_research_common.py`.
The approved plan explicitly enumerated `_research_common.py`'s contents and did not include any
of these node-level methods there, and each individual method is small — not worth a shared base
class or mixin the plan didn't ask for (YAGNI/simplicity bias). Code-quality review (round 1)
flagged this duplication as a maintainability concern; leaving it as two flat, independently
readable node files rather than introducing a shared intermediate base class between them and
`LLMNode` is the simplest correct read of the plan, but a future task is free to extract one if
a third research-style node ever lands with the same shape.

Decided (scope note): fixing T-017's own `folders:` (`src/nodes/llm/`, `config/agents/`,
`config/prompts/`) turned `literature_researcher`/`web_researcher` from `NoOpNode` placeholders
into real `LLMNode`s that build a `SearchClient` and a `RagStore` on first use. Two pre-existing
tests outside those folders — `tests/integration/phases/test_phase_subgraphs_smoke.py` and
`tests/unit/graph/test_checkpointer.py` — only mocked `LLMFactory` and therefore started
attempting real network calls (arxiv/Tavily) and a real Chroma client construction
(`workspace.chroma_host`/`chroma_port` from `config/settings.yaml`, unreachable outside Docker)
once these nodes stopped being no-ops. Extended both tests' existing `_mock_llm`
autouse fixtures to also patch `LiteratureSearchClient`/`WebSearchClient` (return no sources)
and `RagStore` (both node modules' import locations), plus added the two nodes' schema-valid
`"[]"` extraction response to each file's `_llm_side_effect` dispatch. Judged this a minimal,
in-kind extension of test-only code needed to keep "all tests pass" (CLAUDE.md quality gate)
true after this task's change, not a design decision requiring a blocker — no production code
outside T-017's folders was touched.
Affects: `tests/integration/phases/test_phase_subgraphs_smoke.py`,
`tests/unit/graph/test_checkpointer.py`.
Discarded: leaving those two tests broken and reporting them as a discovery for another agent —
would ship T-017 with a failing test suite, violating "never skip verification."

## 2026-08-07 — T-018 [pipeline-agent]
Decided: dropped the original task file's "Kaggle forum posts" scope for `competition_analyst`
entirely (no stub, no partial implementation) and instead added
`kaggle_client.list_top_kernels(competition, n=10, api=None)` — a small, additive function
mirroring `download()`/`submit()`'s existing validation/default-api pattern — as the only new
`kaggle_client.py` surface. `competition_analyst` extracts winning patterns from these kernels'
title/author/vote-count metadata only.
Why: confirmed against the installed `kaggle`/`kagglesdk` packages that no discussions/forum RPC
client is wired into `KaggleApi` — there is no way to list/fetch forum posts through the installed
SDK without scraping kaggle.com HTML, which is out of `kaggle_client.py`'s "thin wrapper around the
kaggle package" contract (see the matching 2026-08-07 T-018 entry in `context/discoveries.md`).
Adding `list_top_kernels` as one small additive function, rather than filing a separate
infra-agent task to design a new Kaggle-facing tool module, was human-approved: it's a low-risk,
purely-additive change to a file `pipeline-agent` doesn't own, and routing it through a dedicated
cross-agent task would have blocked T-018 on infra-agent scheduling for a few lines of code.
Affects: `src/tools/kaggle_client.py` (additive only — `download`/`submit`/`get_score` signatures
unchanged), `src/nodes/llm/competition_analyst.py`.
Discarded: stubbing forum-post fetching now against a future SDK version — no reliable way to test
or validate an unimplemented API surface, and it would have shipped a misleading placeholder.

## 2026-08-07 — T-018 [pipeline-agent] (correction, review round 1)
Decided: refactored `competition_analyst.py` to import and reuse
`src.nodes.llm._research_common`'s `extract_json_array`/`build_index_documents`/
`render_report_markdown` (the same shared helpers `literature_researcher.py`/`web_researcher.py`
already use), deleting the local `_strip_outer_fence`/`_extract_json_array`/
`_build_index_documents`/`_coerce_str_list`/`_coerce_relevance_score`/`_render_report_markdown`
this task's initial implementation had written instead. `KernelSummary` is now adapted into
`_research_common.SourceDocument(title, text, url)` before being handed to the shared helpers.
Why: the initial implementation's premise — that `_research_common.py` "did not exist yet on this
branch" — was factually wrong. T-017 merged `_research_common.py` (commit c4cc8c2) *before* T-018
was even claimed (commit 9b776ac, 3 minutes later); it was present in `src/nodes/llm/` and already
imported by both research nodes the whole time this task was being implemented. Three independent
reviewers (code-quality, security, adversarial) converged on the same root cause. The local
duplicate was strictly weaker in ways that mattered: its `_coerce_relevance_score` did a bare
`float(value)` with no `[0.0, 1.0]` range check (unlike `_research_common._validate_relevance_score`),
verified end-to-end to let an out-of-range or `inf`/`nan` `relevance_score` — attacker-influenceable
via a gamed kernel title, per this node's own untrusted-input threat model — reach the RAG store
unchanged or get silently dropped by Chroma with no audit trail (HIGH severity); its index handling
didn't require extraction indices to exactly cover `1..len(kernels)`, so a duplicate index could
silently double-index one kernel while an omitted one was silently dropped; and its
`_coerce_str_list` coerced non-string list items via `str()` instead of raising. Reusing the shared
helpers closes all three gaps for free and shrank `competition_analyst.py` from 114 to 56
statements.
Affects: `src/nodes/llm/competition_analyst.py`, `tests/unit/nodes/llm/test_competition_analyst.py`
(added tests proving duplicate-index/out-of-range-relevance-score/non-string-list-item now raise,
matching `_research_common.py`'s own strictness).
Discarded: nothing — this is a straightforward "use the code that already existed" fix, not a
design tradeoff.

## 2026-08-10 — T-019 [pipeline-agent]
Decided: `memory_manager` (`src/nodes/llm/memory_manager.py`) implements "consolidate the RAG
store" as **query-window consolidation**, not a corpus-wide scan/delete. `RagStore`
(`src/tools/rag.py`, T-008's protected contract) exposes only `index()` (upsert-by-id) and
`query()` (similarity search) — there is no list-all-documents or `delete()` API to scan and prune
an entire Chroma collection. So the node queries a representative window of candidates
(`_QUERY_N_RESULTS = 20`, wider than `RagStore.query`'s own default of 10), has the LLM partition
that window into clusters of near-duplicates, and merges each cluster into one consolidated
`IndexDocument` re-indexed under a reused `.id` (the canonical member's original id, chosen as the
lowest original 1-based index in the cluster) — `RagStore.index()`'s upsert-by-id semantics
collapse that row in place. This was a human-approved scope adjustment (Architect + user sign-off)
against the task file's more ambitious "deduplicates near-identical entries... reduces them to
one" wording.

Caveat, stated explicitly rather than hidden: non-canonical sibling ids are **not** physically
deleted, since `RagStore` has no `delete()`. When a cluster of N candidates merges into 1
consolidated document, only the canonical id's row gets the merged content on upsert; the other
N-1 candidates' original rows remain in Chroma, stale, and will keep surfacing in future `.query()`
calls until a future `RagStore`/`IndexDocument` enhancement adds deterministic ids and/or delete
support. This is the same underlying gap as the `context/discoveries.md` T-017 OPEN entry
(`IndexDocument.id` defaulting to a random `uuid4()` with no content-based dedup) — not duplicated
here, just cross-referenced: whoever picks up that discovery (deterministic ids and/or a
delete-capable `RagStore`) should revisit `memory_manager`'s canonical-id-reuse-on-upsert merge
mechanism too, since a delete-capable store would let it fully retire non-canonical rows instead of
leaving them stale.

Also: a near-duplicate that the query window never surfaces (i.e. it isn't similar enough to the
query text to rank among the top `_QUERY_N_RESULTS` results) is left untouched by a given pass —
this is a narrower guarantee than "the whole store is deduplicated," and is documented as such in
the module's docstring.
Affects: `src/nodes/llm/memory_manager.py`, `config/agents/memory_manager.yaml`,
`config/prompts/memory_manager/v1.md`. Related to `context/discoveries.md`'s T-017 OPEN entry on
`IndexDocument.id` generation.
Discarded: corpus-wide scan/delete consolidation (the task file's literal wording) — not
achievable without changing `RagStore`/`IndexDocument`, both protected contracts out of this
task's `folders:` (`src/nodes/llm/`, `config/agents/`, `config/prompts/`) and requiring separate
human approval.

## 2026-08-10 — T-019 [pipeline-agent] (post-review follow-up)
Decided: extracted `read_problem_type` and `build_ml_techniques_query` out of
`literature_researcher.py`, `web_researcher.py`, and `memory_manager.py` into
`src/nodes/llm/_research_common.py` as plain functions taking `state: LabState`. All three
node-local copies of both functions were byte-for-byte identical (confirmed via `diff` across all
three files before extracting — including `_build_query`'s query-string phrasing, which this task's
own approved plan had copied verbatim from `literature_researcher` for `memory_manager`, so there
was no per-node phrasing to preserve). This crosses the extraction threshold the 2026-08-07 T-017
decisions.md entry explicitly set: hoisting was called "not worth it" for *two* copies but
reasonable "if a third research-style node ever lands with the same shape" — `memory_manager`
(T-019) is that third node. (`competition_analyst`, T-018, never had this pair of methods, so it
was unaffected either way.) Each node's `_build_messages` now calls
`build_ml_techniques_query(state)` directly instead of `self._build_query(state)`/
`self._read_problem_type(state)`; the two now-redundant instance methods were deleted from all
three node files rather than kept as thin pass-throughs.
Why: removes ~34 lines of triplicated logic (validated correct three separate times instead of
once) with no behavior change — same fallback semantics, same query string, same
`OSError`-tolerant file read. Flagged as non-blocking by code-quality and adversarial review.
Affects: `src/nodes/llm/_research_common.py` (new `read_problem_type`/`build_ml_techniques_query`),
`src/nodes/llm/literature_researcher.py`, `src/nodes/llm/web_researcher.py`,
`src/nodes/llm/memory_manager.py`, and their three test files plus `test_research_common.py` (the
`WorkspaceManager` patch target used by these two functions' file I/O moved from each node's own
module to `src.nodes.llm._research_common`, since that's where the call now lives; direct unit
tests for both functions were also added to `test_research_common.py` against a real
`tmp_path`-backed `WorkspaceManager`, no mocking needed).
Discarded: extracting only `_read_problem_type` and leaving `_build_query` local per-node (the
original, more conservative plan handed down for this follow-up) — turned out unnecessary once
`diff` showed `_build_query`'s output string is *also* identical across all three nodes, not just
`_read_problem_type`; extracting both is strictly simpler than extracting one and leaving the other
as a one-line pass-through to the shared `read_problem_type`.

## 2026-08-10 — T-020 [pipeline-agent]
Decided: hoisted `_relative_to_workspace` (previously duplicated identically in `problem_framer.py`,
`leakage_auditor.py`, `analysis_critic.py`) into a public `relative_to_workspace()` function in
`src/nodes/llm/base.py`, alongside the existing standalone `trim_context`. All three call sites
migrated to import it; `baseline_designer.py` uses it directly, no local copy.
Why: `baseline_designer` needed the exact same absolute-path-written-by-upstream-node /
relative-path-required-by-`WorkspaceManager.read_*` handling (see the 2026-08-07 T-014 discovery),
and a fourth private copy would cross the duplication threshold already flagged as worth hoisting
once a third landed (2026-08-07 T-017 entry, 2026-08-10 T-019 entry).
Affects: src/nodes/llm/base.py, src/nodes/llm/problem_framer.py, src/nodes/llm/leakage_auditor.py,
src/nodes/llm/analysis_critic.py, src/nodes/llm/baseline_designer.py.
Discarded: `src/nodes/llm/_research_common.py`'s own separate `relative_to_workspace` copy
(Phase 2 research nodes) was intentionally left untouched — merging it into `base.py`'s copy is
out of scope for T-020 and not worth the diff risk for an already-landed, already-tested module.

## 2026-08-10 — T-020 [pipeline-agent]
Decided: `baseline_designer` degrades missing/unreadable `problem_definition_path`/`eda_report_path`
to a placeholder string (`"(problem definition not yet available)"` etc.) instead of raising,
mirroring `validation_strategist._read_upstream_context`/`analysis_critic._read_target_content`.
Why: Pipeline Phase 3 can legitimately be exercised standalone (e.g. `tests/integration/phases/
test_phase_subgraphs_smoke.py`'s per-phase parametrization, or a future partial-resume path) without
Phase 1 having populated those `LabState` fields yet; raising would make the node uninvokable in
isolation, unlike every other node it's patterned after.
Affects: src/nodes/llm/baseline_designer.py.

## 2026-08-10 — T-020 [pipeline-agent]
Decided: `baseline_runner`'s generated training script is a fixed Python string constant, not built
per-`design` via string interpolation — it re-reads `experiments/baseline/design.json` and
`validation/fold_config.json` itself from the subprocess's own cwd, so LLM-authored `design` values
(model name, hyperparameters) only ever enter the subprocess as parsed JSON data, never as
interpolated source text.
Why: avoids any code-injection/escaping concern from embedding LLM-authored content directly into
executed Python source. Confirmed clean by T-020's security review.
Affects: src/nodes/compute/baseline_runner.py.

## 2026-08-10 — T-020 [pipeline-agent, fix after adversarial review]
Decided: (1) `_TRAINING_SCRIPT`'s feature selection excludes `target_column` from `feature_columns`
unconditionally, in both the `features == "all"` and explicit-list branches — previously only the
`"all"` branch excluded it, so an LLM-authored explicit `features` list that accidentally included
the target column trained (and silently permanently benchmarked) a model using `y` as a feature.
(2) `build_model()`'s dispatch raises a clear `ValueError` naming the unrecognized model string
instead of silently falling back to `GradientBoosting` — a silent fallback either crashes
confusingly (hyperparameters meant for the real model don't match the fallback's constructor) or,
worse, trains and permanently logs a benchmark under the wrong, unrecorded model identity. The
actually-instantiated model class name is now also recorded in `results.json` and MLflow params
(`model_class`), not just `design["model"]`'s stated name, making the record self-verifying even
when the name did resolve correctly.
Why: found by T-020's adversarial review as two BLOCKER-severity findings — both silent, both
capable of corrupting the pipeline's single permanent baseline benchmark with no error surfaced
anywhere. Added defense-in-depth validation in `baseline_designer._validate_design` rejecting a
design whose explicit `features` list already contains `target_column`, so a leaking design is
never written to disk as the LLM's stated intent in the first place. Added real-`execute()`
(non-mocked subprocess) regression tests for both — every prior `baseline_runner` test mocked
`execute()` entirely, which is exactly why neither bug had test coverage before this review.
Affects: src/nodes/compute/baseline_runner.py, src/nodes/llm/baseline_designer.py,
tests/unit/nodes/compute/test_baseline_runner.py, tests/unit/nodes/llm/test_baseline_designer.py.

## 2026-08-10 — T-020 [pipeline-agent]
Decided: `baseline_runner`'s CV score is each fitted estimator's own `.score()` (accuracy for
classifiers, R² for regressors, chosen via a `y.nunique() <= 20` heuristic), not a metric derived
from `problem_definition.json`'s `success_metric` field (e.g. `roc_auc`, `rmse`, `logloss`).
Why: `baseline_runner`'s only inputs are `design.json` and `fold_config.json`, neither of which
carries the competition's actual success metric; threading it through would mean either
`baseline_designer` copying it into `design.json` (scope creep beyond T-020's "Delivers") or
`baseline_runner` reading `problem_definition.json` directly (a third node reading that file with
its own relativization handling). Deferred as a known v1 simplification, flagged by both the
code-quality and adversarial reviews — worth a follow-up task if later iterations' scores (compared
against this baseline per design.md's Phase 3 description) turn out not to be apples-to-apples.
Affects: src/nodes/compute/baseline_runner.py.

## 2026-08-10 — T-020 [pipeline-agent, non-blocking follow-up noted]
Not fixed in T-020, flagged by adversarial review as follow-up-worthy: `baseline_runner` runs the
actual training (`execute()`, potentially expensive) before its MLflow calls
(`set_tracking_uri`/`start_run`/`log_params`/`log_metric`), and `ComputeNode.__call__` has no
exception handling — an MLflow failure (e.g. unreachable tracking server) after a successful
training run raises uncaught, so `results.json` is never written and a genuinely-computed baseline
score is lost, with no state persisted to avoid recomputing it on retry. Neither `baseline_designer`
nor `baseline_runner` checks whether `design.json`/`results.json` already exist before writing —
the "runs only at `current_iteration == 0`, never re-run, never overwritten" invariant (CLAUDE.md
#4) is enforced entirely by supervisor phase-gating outside this diff, no defense-in-depth at the
node level. Consider for a future task: write `results.json` before/independent of the MLflow
block (or wrap MLflow calls in try/except-and-warn), and/or an existence check before writing either
JSON file.
Status: open, not blocking.

## 2026-08-11 — T-021 [pipeline-agent]
Decided: `solution_architect` v1 implements neither of two inputs/behaviors design.md's Phase 4
description and the task file mention: (1) it does not read a "previous error diagnosis" input —
`error_analyst` (T-031, Phase 6) does not exist yet and `LabState` has no field for its output;
(2) it does not implement "may consult the advisor role" on high-risk decisions — no existing
`LLMNode` in the repo has a precedent for a node dynamically invoking a second model role
mid-execution, and nothing in the task's "Done when" checklist tests it. Both are human-approved
descopes. v1's only inputs are RAG findings (`RagStore.query`) and the baseline results
(`state["baseline_results_path"]`); model_role is fixed to `reasoning`. A one-line pointer to the
error-diagnosis gap lives in `solution_architect.py`'s module docstring for whoever lands T-031.
Why: building a read path for a producer that doesn't exist yet is dead code with no way to test
it meaningfully; dynamic dual-model-role invocation is a new mechanism with no precedent and no
test requirement — both are scope growth beyond a size-S task, better done as deliberate follow-up
work once there's a concrete need.
Affects: src/nodes/llm/solution_architect.py, tasks/in-progress/T-021-node-solution-architect.md.

## 2026-08-11 — T-022 [pipeline-agent]
Decided: `feature_engineer`'s `encodings` validator rejects a target-encoding entry whose
`fold_aware` key is either absent or explicitly `false` — only `"fold_aware": true` (the literal
boolean, not merely present as a key) is accepted.
Why: a spec asserting `"fold_aware": false` on a target-encoding entry is asserting a
leakage-prone design outright, not just omitting metadata — treating that the same as a missing
key (reject both) is the only reading consistent with the T-022 done-when requirement that the
spec explicitly mark target encoding as fold-aware whenever it's used.
Affects: `src/nodes/llm/feature_engineer.py` (`_validate_encodings`).
Discarded: accepting `fold_aware: false` as merely "not yet marked" and only rejecting a missing
key — rejected because it would let an LLM response explicitly declare a leaky design and still
pass validation.

## 2026-08-11 — T-022 [pipeline-agent] (correction, adversarial review)
Decided: whether an `encodings` entry counts as "target encoding" (and therefore requires
`fold_aware: true`) is decided by a word-boundary regex match against a curated tuple of
target-encoding-family phrases (`_TARGET_ENCODING_KEYWORDS`: `target encoding`, `target mean`,
`smoothed target`, `mean encoding`, `leave one out`, `loo`, `woe`, `weight of evidence`,
`catboost`, `james stein`, `m estimate`, `impact encoding`), matched against a separator-normalized
(`-`/`_` collapsed to spaces) lowercase copy of the `method` string — not a bare case-insensitive
substring match on `"target"` alone, and not an exact match against the canonical `target_encoding`
value the prompt names.
Why: the original bare-substring-on-`"target"` approach (first implementation) was confirmed by
adversarial review to be both under-inclusive — common `category_encoders`-library synonyms for
the same leakage-prone technique (`mean_encoding`, `leave_one_out`, `WOE`, `CatBoost`,
`James-Stein`, `M-estimate`, `impact_encoding`) contain no "target" substring and silently bypassed
the fold-aware requirement entirely — and over-inclusive — a method merely mentioning "target"
incidentally (e.g. `frequency_encoding_excluding_target_leak`) was wrongly forced to declare
`fold_aware`. The curated-keyword/word-boundary approach fixes both directions at once, with
regression tests for each. `config/prompts/feature_engineer/v1.md` now names these recognized
terms explicitly so prompt and validator vocabulary agree.
Affects: `src/nodes/llm/feature_engineer.py` (`_TARGET_ENCODING_KEYWORDS`,
`_is_target_encoding_method`, `_validate_encodings`), `config/prompts/feature_engineer/v1.md`,
`tests/unit/nodes/llm/test_feature_engineer.py`.
Discarded: exact string match against `"target_encoding"` (too brittle against the LLM's actual
phrasing freedom); the original bare-substring-on-`"target"` approach (superseded, see above).

## 2026-08-11 — T-022 [pipeline-agent]
Decided: `feature_engineer._read_eda_report` is its own private copy, duplicated from
`baseline_designer._read_eda_report` rather than imported/shared.
Why: follows the established per-module-duplication convention for these upstream-artifact
reader helpers — T-020 hoisted only `relative_to_workspace` itself into `src/nodes/llm/base.py`
and deliberately left the `_read_*` reader functions (which combine that helper with a
node-specific placeholder message and state-field name) duplicated per module; `feature_engineer`
follows the same pattern rather than introducing a new shared-helper precedent unilaterally.
Affects: `src/nodes/llm/feature_engineer.py` (`_read_eda_report`, `_read_solution_plan`).
Discarded: importing/reusing `baseline_designer._read_eda_report` directly — would break the
established convention and create an implicit cross-module dependency between two otherwise
independent node modules.

## 2026-08-11 — T-023 [pipeline-agent]
Decided: `specialist_selector`'s 4-branch keyword precedence checks timeseries keywords
(`"time series forecasting"`, `"forecast"`, `"arima"`, `"prophet"`) and NLP keywords (`"nlp"`,
`"text"`, `"bert"`, `"transformer"`, `"tfidf"`, `"tf idf"`, `"embedding"`) *before*
deep-learning keywords (`"neural"`, `"cnn"`, `"rnn"`, `"deep learning"`, `"pytorch"`, `"lstm"`) —
so a plan naming an LSTM/transformer for a forecasting or text problem routes to
`timeseries_specialist`/`nlp_specialist`, never `deep_learning_specialist`.
Why: LSTM/transformer/CNN are architecture signals, not problem-type signals — they legitimately
co-occur with either a forecasting or an NLP problem (an LSTM is a common forecasting model
choice; a transformer is a common NLP model choice), so treating them as lower-precedence than
the problem-type keywords means routing follows the more actionable specialist boundary (what
domain expertise the specialist needs — time-series or text handling — not which network
architecture happens to be named). Reordering these branches would misroute exactly the plans
where a deep-learning architecture is the *implementation detail* of a timeseries/NLP solution,
not evidence of a distinct "deep learning" problem domain.
Affects: `src/nodes/compute/specialist_selector.py` (`_TIMESERIES_KEYWORDS`, `_NLP_KEYWORDS`,
`_DEEP_LEARNING_KEYWORDS`, `_select_by_signal`).
Discarded: checking deep-learning keywords first (or scoring/ranking all matched keywords instead
of a fixed precedence) — would route an LSTM-based forecasting plan to `deep_learning_specialist`
instead of `timeseries_specialist`, losing the timeseries-specific domain expertise (fold-aware
CV for temporal data, seasonality handling, etc.) the plan actually needs.

## 2026-08-11 — T-023 [pipeline-agent]
Decided: trimmed `config/phases/phase5_implementation.yaml`'s `nodes`/`sequence` to
`[specialist_selector, coder, code_critic]`, removing the 5 specialist names
(`classical_ml_specialist`, `deep_learning_specialist`, `nlp_specialist`, `timeseries_specialist`,
`ensemble_specialist`) that were previously listed there.
Why: `src/graph/phases/generic.py` chains every name in `sequence` into real, always-executed
graph edges. Leaving all 5 specialist names in the YAML would make the compiled Phase 5 subgraph
invoke every specialist for real once T-024–T-028 land, *in addition to*
`specialist_selector`'s own internal one-specialist `resolve_node` dispatch (T-023) — double
work, and a direct contradiction of design.md's "one specialist at a time." `specialist_selector`
now owns specialist dispatch entirely internally, the same pattern `analysis_critic`'s
retry-target dispatch already uses without ever appearing twice in its own phase's `sequence`
(see the 2026-08-05 T-009 entry above).
Affects: `config/phases/phase5_implementation.yaml`, `tests/unit/graph/test_phase_yaml_contracts.py`
(`EXPECTED["phase5_implementation"]["nodes"]`), `docs/pipeline.md` (Supervisor section + new
"Implementation (Phase 5)" subsection).
Discarded: leaving the 5 specialist names in `sequence` and relying on `specialist_selector`'s
internal dispatch alone to make the duplicate invocations harmless (e.g. specialists no-op if
not selected) — rejected because it requires every future specialist implementation (T-024–T-028)
to defensively special-case "was I actually selected this iteration," rather than making
non-selection structurally impossible by not being a graph edge at all.
## 2026-08-11 — T-024 [pipeline-agent]
Decided: the experiment id in `experiments/exp_{id}/design.json` is `state["current_iteration"]`,
resolved by the inherited `LLMNode._resolve_output_path` from
`output_file_pattern: "experiments/exp_{iteration}/design.json"` — no new code path at all.
Why: the task file's `exp_{next_id}` wording implies an allocator, but every mechanism for
producing a "next id" crosses a protected contract: a `WorkspaceManager.next_experiment_id()`
method (infra-agent's `WorkspaceManager` public API), a new `LabState` field (`src/state.py`), or
a `config/settings.yaml` counter. `current_iteration` is already the pipeline's per-cycle counter
and already the placeholder every other iteration-scoped node uses
(`design/iteration_{iteration}/…`, `reports/competition_analysis_iter{iteration}.md`).
Human-approved during planning.
Affects: `config/agents/classical_ml_specialist.yaml`.
Discarded: an id allocator / `WorkspaceManager.next_experiment_id()` / a new `LabState`
`next_experiment_id` field — all protected-contract changes, out of scope for a node task. The
consequence (today `current_iteration` is never incremented, so every cycle overwrites
`experiments/exp_0/design.json`) is logged as an open discovery rather than worked around here.

## 2026-08-11 — T-024 [pipeline-agent]
Decided: `src/nodes/llm/_experiment_design.py` was created up front, with only one consumer
(`classical_ml_specialist`), rather than waiting for the usual "hoist at the third copy" threshold
this repo has applied to `relative_to_workspace` (T-020) and `_research_common` (T-017/T-019).
Why: T-024 was scoped to *define* the `design.json` contract for T-025–T-028 (the four remaining
specialists) and T-029 (`coder`, the consumer). The shared module is the deliverable, not a
speculative abstraction — four sibling tasks are already written against it. Follows the
`_research_common.py` precedent for a shared, non-node helper module under `src/nodes/llm/`,
including its "declares no class matching its own filename stem" note so
`node_resolver._find_node_class` never mistakes it for a node.
Affects: `src/nodes/llm/_experiment_design.py`, `src/nodes/llm/classical_ml_specialist.py`.
Discarded: keeping the validator private to `classical_ml_specialist.py` and letting T-025–T-028
copy it — four divergent copies of a schema that `coder` has to parse is exactly the failure this
module prevents.

## 2026-08-11 — T-024 [pipeline-agent]
Decided: the module boundary is "anything that shapes `design.json` lives in
`_experiment_design.py`; node-local `_read_*` helpers that assemble free-text prompt context stay
duplicated per module". `classical_ml_specialist._read_solution_plan` is therefore its own copy of
`feature_engineer._read_solution_plan`, not an import.
Why: keeps the shared module's surface exactly the contract T-029 depends on, and follows the
established per-module-duplication convention for upstream-artifact readers (2026-08-11 T-022
entry). `read_fold_summary` is deliberately on the *other* side of that line — what it omits
(`fold_indices`) is a contract decision about the frozen folds, not a per-node prompt choice.
Affects: `src/nodes/llm/classical_ml_specialist.py`, `src/nodes/llm/_experiment_design.py`.
Discarded: importing `feature_engineer._read_solution_plan` (implicit cross-module dependency
between two independent node modules). T-025–T-028 may hoist `_read_solution_plan` into
`_experiment_design.py` once a third copy exists.

## 2026-08-11 — T-024 [pipeline-agent]
Decided: forbidden cross-validation keys (`cv`, `cv_strategy`, `folds`, `fold_indices`, `n_folds`,
`n_splits`, `validation`, `test_size`, `shuffle`) are rejected **loudly** — by exact key name, at
the top level and inside `search_space`/`fixed_params`, checked before any other validation —
rather than silently dropped by the whitelist rebuild.
Why: the whitelist rebuild alone would make "the design does not redefine CV" vacuously true and
untestable. A loud rejection makes it an assertable behavior (one parametrized test per key per
location) and gives the LLM a corrective error instead of silently discarding its intent.
`cv_strategy_ref` is deliberately outside the forbidden set: matching is exact-name, and that key
is the pipeline's own injected pointer to the frozen folds.
Affects: `src/nodes/llm/_experiment_design.py` (`FORBIDDEN_CV_KEYS`, `_reject_forbidden_cv_keys`),
`config/prompts/classical_ml_specialist/v1.md`.
Discarded: substring matching (would catch `cv_strategy_ref` and any parameter merely containing
`shuffle`); silently dropping the keys.

## 2026-08-11 — T-024 [pipeline-agent]
Decided: `model_family` is normalized to a canonical token by whole-phrase word-boundary matching
against a per-specialist alias table (the approach of
`feature_engineer._is_target_encoding_method`), and a value matching **two or more** families
(e.g. `"xgboost or lightgbm"`) is **rejected**, not resolved by precedence.
Why: `coder` (T-029) dispatches on this exact string, so the written value must be canonical, and a
wrong-but-plausible pick from an ambiguous response is worse than a loud failure — the specialist
is cheap to re-run, a whole experiment trained on the wrong family is not. Alias matching absorbs
the LLM's real phrasing variance (`xgb`, `LGBM`, `light-gbm`, `ExtraTrees`) without accepting
genuinely unsupported families (`random_forest`, `neural_network`).
Affects: `src/nodes/llm/_experiment_design.py` (`normalize_model_family`),
`src/nodes/llm/classical_ml_specialist.py` (`_MODEL_FAMILIES`).
Discarded: exact match against the four canonical tokens (too brittle); first-match-wins precedence
on ambiguity (silently picks a family the plan may not have meant).

## 2026-08-11 — T-024 [pipeline-agent]
Decided: `search_space` must be non-empty, and `log: true` combined with `step` is rejected — both
enforced by the validator at design time.
Why: both fail later otherwise, and much more expensively. An empty search space produces an Optuna
study with nothing to optimize (a silently meaningless experiment); `log`+`step` raises inside
`suggest_int`/`suggest_float` at trial time, i.e. inside `code_executor`'s subprocess, long after
the design was accepted. Validating the grammar declaratively (types, bounds, finiteness,
`low < high`, positive `step`, `low > 0` under `log`) also keeps validation completely eval-free —
no expression strings are ever parsed or executed.
Affects: `src/nodes/llm/_experiment_design.py` (`_validate_search_space`, `_validate_numeric_param`,
`_validate_log`, `_validate_numeric`), `config/prompts/classical_ml_specialist/v1.md`.
Discarded: accepting Optuna expression strings / distribution-call strings / bare 2-tuples — all
would require either `eval` or a bespoke mini-parser to interpret downstream.

## 2026-08-11 — T-024 [pipeline-agent]
Decided: `feature_spec_ref` is relativized against the workspace root (via `relative_to_workspace`)
before being written into `design.json`, with a `design/iteration_{current_iteration}/
feature_spec.json` fallback when `state["feature_spec_path"]` is unset. It is never `""`.
Why: `LabState` path fields hold the *absolute* path `WorkspaceManager.write_json` returned. Baking
a host-absolute path into `design.json` breaks silently inside `code_executor`'s subprocess (which
runs with the workspace as its cwd) and, in production, inside the container, where the workspace
is bind-mounted at `/competitions/{name}` rather than at the host path that was recorded.
Affects: `src/nodes/llm/_experiment_design.py` (`resolve_feature_spec_ref`).
Discarded: storing the absolute path verbatim; omitting the key when unset (T-029 would then have
to reinvent the fallback).

## 2026-08-11 — T-024 [pipeline-agent]
Decided: the resolved `feature_spec_ref` is stashed on the node instance in `_build_messages` and
read back in `_write_output`; an unset stash raises a `ValueError` naming the node rather than
defaulting to something plausible.
Why: `LLMNode.__call__` (`src/nodes/llm/base.py:83-94`) never passes `state` to `_write_output`, so
a value derived from `state` can only reach it through the instance — the same mechanism
`literature_researcher` uses for `self._sources`. Raising on an unset stash keeps the only way to
reach that state (calling `_write_output` outside `__call__`'s order) a loud failure instead of a
`design.json` silently pointing at the wrong feature spec.
Affects: `src/nodes/llm/classical_ml_specialist.py` (`_feature_spec_ref`).
Discarded: recomputing the ref inside `_write_output` (would need a second `WorkspaceManager` and
the `state` it doesn't have); defaulting silently to the iteration pattern.

## 2026-08-11 — T-024 [pipeline-agent]
Decided: no new `LabState` field for the written design path, and no change to
`config/phases/phase5_implementation.yaml`.
Why: `coder` (T-029) reads `experiments/exp_{iteration}/design.json` from its well-known path, the
convention `baseline_designer`/`baseline_runner` and `validation/fold_config.json` already use —
`_build_output_state` therefore stays the base class's `{}`. The phase YAML is a protected contract
and, separately, must not list the specialists at all: `specialist_selector` (T-023) dispatches to
exactly one of them internally, so a phase-YAML entry would execute the node a second time as a
real graph edge.
Affects: `src/nodes/llm/classical_ml_specialist.py`, `docs/agents.md`, `docs/pipeline.md`.
Discarded: a `current_design_path` `LabState` field (protected contract, and redundant with a fixed
path).

## 2026-08-11 — T-024 [pipeline-agent]
Decided: no logging statements were added to either new module.
Why: no node under `src/nodes/` logs today — observability is a graph-level concern wired through
`src/observability/` (the JSONL callback handler), not something individual nodes do. Introducing
per-node logging in a node task would set a new convention unilaterally across ~26 nodes. Error
context is instead carried by the `ValueError` messages, every one of which names the specialist,
the offending field, and the offending value.
Affects: `src/nodes/llm/_experiment_design.py`, `src/nodes/llm/classical_ml_specialist.py`.
Discarded: adding a module-level `logging.getLogger(__name__)` to the new modules only.

## 2026-08-12 — T-024 [pipeline-agent] (review round)
Decided: `preprocessing` entries must match `^[a-z][a-z0-9_]{0,63}$` — a lower_snake token that
*names* a step, never a string that expresses one.
Why: the original "non-empty string" rule let adversarial review write
`["StratifiedKFold(n_splits=3, shuffle=True)", "train_test_split(X, y, test_size=0.2)"]` to disk —
a cross-validation redefinition that slips past `FORBIDDEN_CV_KEYS` entirely, because that guard
matches dict *keys* and this hid in a list *value*. It therefore defeated T-024's own "does not
redefine CV" acceptance criterion. A token shape (rather than a closed vocabulary of allowed
techniques) is the narrowest fix that closes it without deciding on behalf of T-025–T-028 which
preprocessing steps their specialists may name.
Affects: `src/nodes/llm/_experiment_design.py` (`_PREPROCESSING_STEP_RE`,
`_validate_preprocessing`), `config/prompts/classical_ml_specialist/v1.md`.
Discarded: an allow-list of known preprocessing step names (would need one union across five
specialists that do not exist yet, and would reject legitimate model-specific steps).

## 2026-08-12 — T-024 [pipeline-agent] (review round)
Decided: every `search_space`/`fixed_params` key must match `^[A-Za-z_][A-Za-z0-9_]{0,63}$`.
Why: parameter names were the one part of the payload passing through completely unvalidated, and
`coder` (T-029) turns them into Python keyword arguments in a script `code_executor` runs on the
host — so a key containing quotes, parentheses, newlines or backslashes is an
injection precondition, not a cosmetic issue. Every real hyperparameter name of xgboost/lightgbm/
catboost/sklearn already satisfies the pattern, so the false-rejection cost is zero.
Affects: `src/nodes/llm/_experiment_design.py` (`_PARAM_NAME_RE`, `_validate_param_name`),
`config/prompts/classical_ml_specialist/v1.md`.

## 2026-08-12 — T-024 [pipeline-agent] (review round)
Decided: two Optuna-semantics rules the library itself does not enforce — `step <= high - low`, and
`choices` deduplicated by **value** (`1`, `1.0` and `True` are one choice) rather than by
`(type_name, value)`.
Why: both were verified by execution against optuna 3.5.0. `suggest_int(low=1, high=2, step=5)`
does not raise; it returns the same value on every trial, so the entire trial budget is spent
without ever tuning that parameter (the float case emits only a `UserWarning`) — the same class of
silent waste this module already rejects `log` + `step` for. And `CategoricalDistribution` maps
`1`, `1.0` and `True` to the same internal index, so a trial that trains on `True` is *recorded* as
`1` and the winning configuration is no longer reproducible by `coder`. The original type-keyed
dedup comment ("so `1` and `True` aren't conflated into a false duplicate") had the consumer's
requirement backwards and is corrected in place.
Affects: `src/nodes/llm/_experiment_design.py` (`_validate_numeric_param`, `_validate_choices`),
`config/prompts/classical_ml_specialist/v1.md`.

## 2026-08-12 — T-024 [pipeline-agent] (review round)
Decided: `extract_json_object` retries **once** on the substring between the first `{` and the last
`}` when the whole-text parse fails, and reports the *original* error if that also fails. This
diverges from the sibling structured-output nodes (`baseline_designer`, `feature_engineer`,
`solution_architect`, `problem_framer`, `leakage_auditor`), which reject outright.
Why: this validator has ~40 distinct reject paths and sits on a node with no retry wrapper — Phase
5's `code_critic` targets `coder`, not the specialists, and `LLMNode.__call__` has no retry of its
own. A single sentence of preamble before a fenced block therefore aborted the entire run.
Tolerance is warranted *because* the downstream validation is unusually strict: the salvage only
widens what reaches the validator, it never weakens what the validator accepts. Deliberately scoped
to this module rather than pushed into `src/nodes/llm/base.py`, which would change five landed
nodes' behavior in a node task.
Affects: `src/nodes/llm/_experiment_design.py` (`extract_json_object`, `_slice_outermost_braces`).
Discarded: a bracket-matching scanner (more code, no benefit — the validator rejects anything the
naive slice gets wrong); making the sibling nodes tolerant too (out of scope, and they are not the
ones with 40 reject paths).

## 2026-08-12 — T-024 [pipeline-agent] (review round)
Decided: every degradation path widened to match its documented "never raises" claim, and every
validation failure guaranteed to be a `ValueError`.
Why: three claims in the module were false as written. `read_fold_summary` caught only `OSError`,
so a truncated/empty `fold_config.json` (`json.JSONDecodeError`), invalid UTF-8
(`UnicodeDecodeError`) or a path outside the workspace root (`ValueError` from
`Path.relative_to`) escaped and killed the run — reproduced through the real phase-5 subgraph. It
now catches `(OSError, ValueError)`, which covers all three, since both decode errors are
`ValueError` subclasses. `resolve_feature_spec_ref` claimed "never returns `""`" but raised on a
foreign absolute path — the exact shape of a resumed run whose workspace moved — and passed a
stored `..` traversal through into `design.json`; both now fall back to the iteration pattern.
And `math.isfinite` raises `OverflowError` on a sufficiently large Python int, so integer bounds
are range-checked against ±2**53 first, and `json.loads` raises a bare `ValueError` (not
`JSONDecodeError`) on an integer literal past CPython's 4300-digit limit, which is now wrapped and
attributed to the specialist. Non-finite floats are also rejected in `_is_json_scalar`, since
`WorkspaceManager.write_json` uses `json.dump`'s default `allow_nan=True` and would otherwise write
a `design.json` that fails `JSON.parse` in the frontend.
Affects: `src/nodes/llm/_experiment_design.py` (`read_fold_summary`, `resolve_feature_spec_ref`,
`_validate_numeric`, `_parse_json`, `_is_json_scalar`).

## 2026-08-12 — T-024 [pipeline-agent] (re-review round)
Decided: a single module-level `DEGRADE_ERRORS = (OSError, ValueError, RecursionError)` tuple,
used by every upstream-artifact reader this PR owns (`read_fold_summary`,
`resolve_feature_spec_ref`, `classical_ml_specialist._read_solution_plan`), plus an
`isinstance(path, str)` guard in front of each.
Why: the previous round hardened `read_fold_summary` and left `_read_solution_plan` — called one
line later in the same `_build_messages` — still catching `OSError` alone, so a truncated
`solution_plan.json` or a moved workspace still aborted the run through the other reader. Naming
the set once makes "all three readers degrade on the same inputs" a property of the module rather
than of whoever last edited a `try` block. `RecursionError` earns its place separately: it is a
`RuntimeError`, so neither `OSError` nor `ValueError` catches a ~993-level nested payload, and the
serialization on the way *out* recurses just as the parse on the way in does — so both sit inside
the guard now, not just the read.
Affects: `src/nodes/llm/_experiment_design.py` (`DEGRADE_ERRORS`, `read_fold_summary`,
`resolve_feature_spec_ref`), `src/nodes/llm/classical_ml_specialist.py` (`_read_solution_plan`).
Discarded: narrowing the "never raises" docstrings to match the code instead — these readers exist
precisely so Phase 5 survives a missing or malformed upstream artifact; a reader that aborts the
graph on a corrupt file is not doing its job.

## 2026-08-12 — T-024 [pipeline-agent] (re-review round)
Decided: `step` is compared against `high - low` with a relative tolerance
(`_STEP_RANGE_TOLERANCE = 1 + 1e-9`), not exactly.
Why: the exact comparison added in the previous round falsely rejected legitimate two-value grids
because binary floating point makes the subtraction lossy — `0.3 - 0.1` is `0.19999999999999998`,
so `low=0.1/high=0.3/step=0.2` was rejected while `low=0.1/high=0.7/step=0.6` passed. Optuna 3.5.0
accepts both. An input-dependent false rejection on a node with no retry wrapper is a self-inflicted
outage on valid input, which is strictly worse than the silent collapse-to-a-constant bug the check
was added for; the tolerance is nine orders of magnitude too small to readmit that bug (a step
several times its range).
Affects: `src/nodes/llm/_experiment_design.py` (`_STEP_RANGE_TOLERANCE`, `_validate_numeric_param`).
Discarded: dropping the check (loses the real bug); `math.isclose` (same intent, but an extra
import and a less obvious asymmetry — the check only ever needs slack in one direction).

## 2026-08-12 — T-024 [pipeline-agent] (re-review round)
Decided: `_is_json_scalar` rejects ints beyond ±2**53, and the salvage in `extract_json_object`
covers fence failures as well as parse failures.
Why: both were fixed at the published reproduction and left reachable one field over. The ±2**53
limit went into `_validate_numeric` (bounds) only, so `fixed_params: {"x": 2**53 + 1}` and
`choices: [10**400]` still reached disk, where `JSON.parse` silently reads them back as
`9007199254740992` and `null` — falsifying `_is_json_scalar`'s own "survives a round trip
unchanged" docstring, the exact defect the non-finite-float rejection was raised for. And the
salvage sat *after* `_strip_outer_fence`, which raises first, so the two most common postamble
shapes (a sentence after a closed fence, a fence the model never closed) still aborted the run —
i.e. the tolerance fix did not cover the case it was written for. The salvage now wraps both steps
and slices the raw response; it stays fail-closed, since it only ever hands `json.loads` one
contiguous substring.
Affects: `src/nodes/llm/_experiment_design.py` (`_is_json_scalar`, `extract_json_object`).

## 2026-08-12 — T-025 [pipeline-agent]
Decided: all five Phase-5 specialists keep writing the same
`experiments/exp_{iteration}/design.json` — no `{specialist}` component is added to
`output_file_pattern`. Human-confirmed at the Phase-1 checkpoint.
Why: T-024 left this open as "a design decision across four unstarted tasks" and T-025 is the first
of them. design.md invariant #7 guarantees `specialist_selector` activates exactly one specialist per
iteration, and `specialist_selector.run` dispatches exactly once with no loop, so no landed code can
produce a collision. Adding the component would force `coder` (T-029) to either glob a directory or
gain a new `LabState` field — and `src/state.py` is a protected contract — buying a
protected-contract conversation for a collision that cannot currently happen. It would also mean
editing the landed `config/agents/classical_ml_specialist.yaml` from a task scoped to a different
node.
Affects: `config/agents/deep_learning_specialist.yaml`, `docs/pipeline.md` (§ The design.json
contract). Escape hatch: if a future task ever runs two specialists in one iteration, it changes the
pattern for all five at once, in one PR.
Discarded: `experiments/exp_{iteration}/{specialist}/design.json` — correct in the abstract, but it
prices in a `LabState` change and a landed-config edit to solve a hypothetical.

## 2026-08-12 — T-025 [pipeline-agent]
Decided: `deep_learning_specialist` declares its own node-local `_MODEL_FAMILIES` table with exactly
three canonical tokens — `tabnet`, `node`, `mlp` — and `src/nodes/llm/_experiment_design.py` is not
modified at all.
Why: `normalize_model_family(value, allowed, specialist)` takes the family table as a *parameter*,
and T-024's own table is node-local, so the shared module is already parameterized for exactly this.
Editing it would put the contract that T-026–T-028 inherit and T-029 consumes into a single-node PR.
`node` (Neural Oblivious Decision Ensembles) is kept as the canonical token for literature fidelity
and symmetry with the other two despite colliding lexically with the pipeline's own "node"
vocabulary. Word-boundary matching keeps the obvious near-misses out (`nodes`, `NODEv2`), and its
spelled-out aliases sit in the same family so `"NODE (Neural Oblivious Decision Ensembles)"` resolves
to one family rather than reading as ambiguous. Correction, flagged by review: the token is **not**
narrower than that — `normalize_model_family` collapses `-`/`_` to spaces *before* matching, so
`node_count`/`node-count` become `"node count"` and DO match. The consequence is over-matching rather
than mis-matching (a descriptive mention of "node" in a single-family answer such as `"TabNet
(attentive node selection)"` is rejected as ambiguous, not silently resolved to the wrong family),
which is the safe failure direction given `coder` dispatches on the written value. Both behaviors are
now pinned by tests. T-026–T-028 reusing this reasoning should note the separator-collapse
interaction when choosing short canonical tokens.
Affects: `src/nodes/llm/deep_learning_specialist.py` (`_MODEL_FAMILIES`).
Discarded: `neural_oblivious_decision_ensembles` as the canonical key — unwieldy in `coder`'s
dispatch and inconsistent with `tabnet`/`mlp`. Also discarded: adding the neural families to a shared
table.

## 2026-08-12 — T-025 [pipeline-agent]
Decided: the "preprocessing steps that need fitting are fitted inside each fold" requirement lives in
the prompt only, not in the validator.
Why: fit scope has no representation in the schema whatsoever — `preprocessing` is a flat list of
tokens — so enforcing it would mean extending `design.json`'s shape, i.e. editing the frozen shared
contract on behalf of four unstarted tasks. The prompt instead requires the fit scope to be visible
in the token itself (`standard_scaler_fitted_per_fold` rather than `standard_scaling`) so the
requirement survives into `coder`'s reading of the design. Logged as an OPEN discovery for T-029/T-031
and cross-referenced to T-047's `fit_scope` work rather than solved unilaterally here.
Affects: `config/prompts/deep_learning_specialist/v1.md` (§ Preprocessing scope),
`context/discoveries.md`.
Discarded: a closed vocabulary of allowed preprocessing tokens — `_PREPROCESSING_STEP_RE` is
deliberately a shape constraint so each specialist picks its own steps, and a whitelist would have to
live in the shared module.

## 2026-08-12 — T-025 [pipeline-agent]
Decided: the prompt directs the LLM to describe architectures through scalar parameters
(`n_layers`, `layer_width`, `width_decay`, `embedding_dim_multiplier`) rather than tuning over
lists, and states the `fixed_params` asymmetry explicitly.
Why: `_validate_choices` → `_is_json_scalar` already rejects a list-valued `choices`, so
`{"type": "categorical", "choices": [[64,32],[128,64]]}` — the natural way to tune layer widths —
fails validation. Since the guard already exists, the prompt's job is to steer the response to a
shape that passes, not to add a second one. `_validate_fixed_params` *does* accept a flat list of
scalars, so a fixed `"hidden_dims": [256, 128]` is legal; leaving that asymmetry unstated would make
the prompt look wrong to anyone reading the validator.
Affects: `config/prompts/deep_learning_specialist/v1.md` (§ Architecture hyperparameters must be
scalars).
Discarded: telling the LLM to encode an architecture as a string (`"64_32"`) — it passes validation
and pushes a private parsing format onto `coder`.

## 2026-08-12 — T-025 [pipeline-agent]
Decided: the task's "activated only when the dataset is large enough" condition is prompt-level, and
the prompt forbids refusing — a small-looking dataset degrades the design instead.
Why: a code-level gate is impossible today, not merely undesirable. `LabState` has no row-count,
shape or dataset-size field, and `specialist_selector` matches keywords against a text blob of
`problem_type`/`model_families`/`order`/`rationale` with no size input anywhere in the selection
path. More importantly, by the time this node runs the selection has already happened and no other
specialist is queued behind it for the iteration, so a refusal would leave Phase 5 with no artifact
at all. The prompt therefore maps the size signal onto capacity (a modest-width `mlp` rather than
`tabnet`/`node`, a smaller search space) and requires the concern to be recorded in `rationale` —
mirroring `classical_ml_specialist`'s "Design something defensible, always".
Affects: `config/prompts/deep_learning_specialist/v1.md` (§ Design something defensible, always).
Discarded: parsing `reports/eda_report.md` for a row count (free-text markdown from `data_analyst`);
adding a size field to `LabState` (protected contract).

## 2026-08-12 — T-025 [pipeline-agent]
Decided: `_read_solution_plan` is copied into this node module — a third copy — rather than hoisted.
Why: T-024's log left the door open to hoist "at the third copy", but the only sensible landing place
is `_experiment_design.py`, frozen for this task, and importing it from `classical_ml_specialist` is
exactly the sibling-to-sibling node dependency T-024 explicitly discarded. The copy carries the wider
`DEGRADE_ERRORS` catch, so it degrades on the same inputs as `read_fold_summary` one line later.
Affects: `src/nodes/llm/deep_learning_specialist.py` (`_read_solution_plan`).
Discarded: hoisting into `base.py` in this PR — it belongs with the extractor hoist already logged as
a discovery, which migrates seven call sites at once.

## 2026-08-12 — T-025 [pipeline-agent]
Decided: `test_specialist_selector.py`'s two "unlanded specialist" tests are re-pointed from
`deep_learning_specialist` to `nlp_specialist`, and a landed-case test is added for this node.
Why: this is a test-safety fix, not cleanup. `test_real_resolve_node_falls_back_to_noop_and_returns_empty_dict`
seeds a `pytorch cnn` plan and runs the **real** selector; the moment this node's module exists,
`resolve_node` discovers it by convention and that test dispatches into a real `LLMNode`, attempting
a live API call from a unit test on any machine with API keys set. `nlp_specialist` is the next
unlanded specialist and the NLP branch precedes the deep-learning branch in `_select_by_signal`, so a
text signal is a stable route. T-024 made the identical move when it landed.
Affects: `tests/unit/nodes/compute/test_specialist_selector.py`.
Discarded: mocking `resolve_node` in those tests — they exist specifically to exercise the *real*
resolver.

## 2026-08-12 — T-025 [pipeline-agent]
Decided: no logging in this node.
Why: consistent with every node under `src/nodes/` (T-024 convention). All error context rides on the
`ValueError` messages, each of which names the specialist, and the JSONL observability layer already
records node entry/exit centrally.
Affects: `src/nodes/llm/deep_learning_specialist.py`.
Discarded: per-node loggers — they would duplicate the observability layer and add a second,
divergent source of run history.

## 2026-08-12 — T-025 [pipeline-agent]
Decided: phase-5 integration coverage is a new dedicated routing test
(`test_phase5_subgraph_routes_neural_plan_to_deep_learning_specialist`) rather than a change to the
existing parametrized phase-5 case.
Why: the parametrized case runs on an unseeded workspace, so the selector's keyword precedence falls
through to its default (`classical_ml_specialist`) — changing it would trade away the default-route
coverage to gain the neural one. And re-pointing the selector unit tests to `nlp_specialist` (above)
removes the repo's only coverage of a *neural* plan travelling through the real selector, so
something had to replace it: the new test seeds a neural plan and asserts keyword branch →
`resolve_node` → real node → file on disk.
Affects: `tests/integration/phases/test_phase_subgraphs_smoke.py`
(`_MOCK_DEEP_LEARNING_DESIGN`, `_llm_side_effect`, the new test).
Discarded: asserting only that the subgraph ran — that was the weakness T-024's review round fixed
for the classical path.
## 2026-08-12 — T-026 [pipeline-agent]
Decided: `nlp_specialist` recognizes exactly **three** canonical `model_family` values —
`tfidf_linear`, `sentence_embeddings`, `transformer_finetune` — rather than the four
`classical_ml_specialist` uses.
Why: chosen at the human checkpoint as the smallest set that separates the three text-modeling
approaches by cost/complexity tier (sparse linear, frozen embeddings, fine-tuned transformer)
without inviting an LLM to pick an ensemble-of-approaches family that `coder` (T-029) would have no
single code path for. Same alias-table mechanism as `classical_ml_specialist`
(`normalize_model_family`, word-boundary matching on a separator-normalized string) — zero changes
to the shared validation contract.

Decided: all five Phase-5 specialists keep writing `experiments/exp_{iteration}/design.json` — the
path scheme is **not** given a specialist-namespaced component. This relies on, and this entry
records as an explicit invariant, "exactly one specialist runs per iteration": true by construction
today at `specialist_selector.py:227-233` (`resolve_node(chosen)(state)` is called exactly once per
`run`). Resolves the `context/discoveries.md` entry logged 2026-08-12 by T-024 ("All five Phase-5
specialists write the same path").
Why: a specialist-namespaced path (`experiments/exp_{iteration}/{specialist}/design.json`) is a
real alternative, but it is a decision affecting four tasks (T-025, T-027, T-028 still unstarted,
plus `coder`/T-029's consumer contract) and nothing in the current single-specialist-per-iteration
design requires it yet. Should the invariant ever need to change (e.g. an ensembling pass reading
two candidate designs in the same iteration), the path scheme decision needs revisiting alongside
it — noted here rather than acted on speculatively.
Note: "exactly one specialist per iteration" is arguably CLAUDE.md-invariant material, but
CLAUDE.md is outside a node task's `folders:` and editing it is a governance change this task does
not make; the human decides separately whether it graduates there.

Decided: `ngram_range` and other tuple-shaped hyperparameters are a **prompt-level convention**,
not a validator change — `nlp_specialist/v1.md` instructs the LLM to express them either as a
`categorical` `search_space` entry whose `choices` are string tokens (`"1-1"`, `"1-2"`, `"1-3"`) or
as a single string token pinned in `fixed_params`, never as a JSON array-of-two.
Why: `_experiment_design._validate_choices`/`_is_json_scalar` correctly reject non-scalar
`choices` entries, and a bare 2-tuple round-trips through JSON as a plain list anyway — Optuna's
`CategoricalDistribution` cannot safely reconstruct a tuple from that list. Solving it in the prompt
keeps the shared validator family-agnostic, consistent with the "shape constraint only, not a
closed vocabulary" framing `_validate_preprocessing`/`FORBIDDEN_CV_KEYS` already use.

Decided: hoisted `read_solution_plan` into `src/nodes/llm/_experiment_design.py`, verbatim from
`classical_ml_specialist._read_solution_plan` (T-024), rather than writing a third node-local copy
for `nlp_specialist`.
Why: this is the third copy of that reader (`feature_engineer`'s divergent `OSError`-only copy is
the first, `classical_ml_specialist`'s `DEGRADE_ERRORS` copy is the second) — the 2026-08-11 T-024
decision-log entry explicitly sanctions the hoist once a third copy exists. `classical_ml_specialist`'s
own private copy is deliberately left in place: it already shipped and is already tested, and
retroactively migrating it is not required by this task.
Affects: `src/nodes/llm/nlp_specialist.py`, `src/nodes/llm/_experiment_design.py`,
`config/prompts/nlp_specialist/v1.md`.
Discarded: a fourth divergent `_read_solution_plan` copy local to `nlp_specialist.py`; a
specialist-namespaced output path for `design.json`.

## 2026-08-12 — T-026 [pipeline-agent]
Decided (post-checkpoint, adversarial review fix): `_MODEL_FAMILIES["transformer_finetune"]` gained
six bare fine-tune-modifier tokens — `"fine tune"`, `"fine tuned"`, `"fine tuning"`, `"finetune"`,
`"finetuned"`, `"finetuning"` — instead of the specific paired combo aliases (e.g. "sentence
transformer finetune", "sbert fine tuning") the review initially suggested as a minimum.
Why: `normalize_model_family` matches by literal contiguous substring, so paired combo aliases can
only catch a fine-tune modifier sitting immediately adjacent to a `sentence_embeddings` term. Three
of the six adversarial phrases the review raised do not have that shape — an intervening word
("fine-tune **the** sentence transformer end to end"), a comma ("sentence transformer**,**
fine-tuned"), and a pluralized/extended noun phrase ("fully fine-tuned **sentence-transformers
model**") — so literal paired aliases cannot reach them no matter how many are added. The bare
modifier tokens are a strict superset: any of the eight paired phrases the review listed, and all
six of its adversarial examples, already contain a bare modifier token *and* a `sentence_embeddings`
alias as two separate substrings, so both families match and the existing ambiguity check in
`normalize_model_family` fires — with no `_experiment_design.py` change, per the review's explicit
boundary. Verified by re-running the alias round-trip check (see this task's `## Completed` section
and the PR): every original alias in all three families, and each new bare modifier token in
isolation, still resolves solely to its own family; the eight paired phrases the review suggested,
and all six adversarial examples, now correctly raise ambiguous.
Residual risk (recorded in the 2026-08-12 T-026 `context/discoveries.md` entry): `normalize_model_family`
still has no longest-match-wins rule, so this is a local mitigation, not a fix — a semantically
clear "fine-tuned sentence transformer" now raises rather than resolving to the intended family.
The general fix belongs in `_experiment_design.normalize_model_family`, out of bounds for this task
per the review (a shared-contract change affecting `classical_ml_specialist` and three unstarted
sibling specialists).
Also strengthened `config/prompts/nlp_specialist/v1.md`: `model_family` must be the bare literal
token, never prose describing the approach (defense in depth — the alias-table fix protects against
an LLM response that already happened to phrase it that way; the prompt change tries to prevent the
phrasing in the first place).
Affects: `src/nodes/llm/nlp_specialist.py` (`_MODEL_FAMILIES`), `config/prompts/nlp_specialist/v1.md`,
`context/discoveries.md`.
Discarded: adding the eight literal paired-combo aliases the review listed as a minimum (they would
have been redundant with the bare modifier tokens and still would not have covered three of the six
adversarial phrases); changing `normalize_model_family`'s matching rule itself (explicitly out of
scope — shared contract).

## 2026-08-13 — B-001 [Orchestrator]
**Orchestrators verify Coder-reported test/coverage numbers themselves; they do not relay them.**
Two reports during T-025/T-026 were wrong in the same direction (optimistic): a genuine test
failure was described as "deselected", and a coverage figure was quoted from a run that had
aborted under `-x` (so the number covered only the tests that ran before the abort, not the
suite). Both underlying claims happened to hold up when re-checked, but the reporting did not.
Consequence adopted here: any number that appears in a PR body, a `## Completed` section, or a
hand-off — pass/fail counts, coverage percentages, mutation scores — must be re-run by the
Orchestrator before it is written down, and a `-x`-aborted run is never a valid source for a
coverage figure. This is a reporting-trust rule, not a competence claim about the Coder role: in
the same episode its one deviation from the Orchestrator's fix instructions was the better call.
Discarded: adding an automated self-report checker (no CI yet — T-044 is still `available`), and
filing this as a bug (it is process, not code).

## 2026-08-13 — B-001 [pipeline-agent]
**`tests/fixtures/` now holds a Python module (`graph_mocks.py`) plus an explicit `__init__.py`.**
design.md's test-tree sketch describes `tests/fixtures/` as data only (`datasets/`, `responses/`,
`workspaces/`), and the directory did in fact hold only `config/` and `prompts/` data until now.
The network-free mock set B-001 had to share has two consumers in *different* trees —
`tests/unit/graph/test_checkpointer.py` and `tests/integration/phases/test_phase_subgraphs_smoke.py`
— so neither can own it, and a `conftest.py` would have to live at `tests/` root (widening the
autouse blast radius to every test in the repo, including the ~880 that mock nothing). An importable
module under `tests/fixtures/` is the narrowest option and is inside B-001's `folders:`.
`__init__.py` was added so `tests.fixtures` is a regular package rather than an implicit namespace
package resolved differently depending on invocation; `tests/` and `tests/unit/` themselves stay
namespace packages, matching the repo's existing partial-`__init__.py` state.
Discarded: a root `tests/conftest.py` fixture (too broad, as above), and duplicating the mock set in
both files (the duplication is what let the two copies drift into this bug in the first place).

## 2026-08-13 — B-001 [pipeline-agent]
**`graph_llm_mocks(analysis_critic_pass=...)` defaults to `False`, i.e. the critic is NOT mocked to
pass unless a caller opts in.** With no `analysis_critic` dispatch branch, the mocked LLM's generic
fallback is normalized to `iterate` by `_parse_verdict`, so the critic really re-invokes its target
`max_retries` times and then forces a pass (CLAUDE.md invariant #5). That is the smoke test's
pre-existing behavior. It is *incidental* rather than asserted — grepping the smoke test for
`analysis_critic|verdict|forced_pass` returns zero hits — but it is real executed coverage of the
retry/forced-pass control flow, and B-001 is a test-correctness bug, not a coverage-reduction task.
Making `pass` the default would have silently deleted that coverage while every test stayed green.
The checkpointer test opts in (`True`) because it counts graph-driven node executions and the
retries would otherwise be miscounted as phase re-execution — the original `assert 4 == 1`.
Discarded: defaulting to `True` (silent coverage loss), and asserting the forced-pass path in the
smoke test to make it explicit (out of B-001's scope; the smoke test is a compile-and-run smoke, and
a real forced-pass assertion belongs in `tests/unit/nodes/llm/test_analysis_critic.py`).

## 2026-08-13 — B-001 [pipeline-agent]
**`_MOCK_LLM_CONTENT`'s fold split changed from `{"train": [0], "val": [1]}` to
`{"train": [0, 1, 2], "val": [3, 4]}`, and the checkpointer test seeds its dataset *before* the
first invoke rather than seeding folds at all.** That constant is dual-purpose: generic fallback for
undispatched nodes AND `validation_strategist`'s fold source, frozen verbatim into
`validation/fold_config.json` (invariant #1, write-once). Once the checkpointer test's resume
reaches phase 3, the real `baseline_runner` trains a real sklearn `LogisticRegression` subprocess
(only `mlflow` is mocked) against those exact indices — and a single-row, single-class training
split dies inside `sklearn/linear_model/_logistic.py`. The new split matches the 5-row CSV that
`seed_raw_train_csv` writes so both splits carry both classes. Verified inert for the smoke test:
each parametrized case gets a fresh `tmp_path` and phase 3 there seeds its own `fold_config.json`.
The folds cannot be pre-seeded in the checkpointer test the way the smoke test's
`_seed_phase3_baseline_fixtures` does, because that test runs phase 1 for real: `validation_strategist`
would either overwrite the seed or raise `FoldsAlreadyFrozenError`. Only `data/raw/train.csv` is
seeded, and it must land before the first invoke since phase 1 reads it.

## 2026-08-13 — B-001 [pipeline-agent]
**`analysis_critic`'s import-time `resolve_node` binding was worked around test-side (patching two
locations) rather than changed in `src/`.** `analysis_critic` does `from src.graph.node_resolver
import resolve_node` at module scope (`src/nodes/llm/analysis_critic.py:31`), so monkeypatching
`node_resolver.resolve_node` — which is what `src/graph/builder.py:70-73` deliberately reads through
— does not reach it once the module is imported. That is exactly what made the failure import-order
dependent. `_install_counting_resolver` now patches both `src.graph.node_resolver.resolve_node` and
`src.nodes.llm.analysis_critic.resolve_node`, making the counts identical regardless of import order.
Discarded: switching `analysis_critic` to call `node_resolver.resolve_node(...)` (the bug file lists
it as optional). It touches `src/`, which the bug's own diagnosis established needs no change, and
`analysis_critic`'s module docstring (lines 16-20) *documents* the `src.nodes.llm.analysis_critic.
resolve_node` patch point as the contract its own unit tests rely on — flipping it would break those
tests for no behavioral gain. If it is ever revisited, it should be its own task with its own
justification, per the bug file.

## 2026-08-14 — T-027 [pipeline-agent]
**`timeseries_specialist` accepts exactly five canonical model families:** `arima`, `prophet`,
`exponential_smoothing`, `gradient_boosting_lags`, `linear_lags` (human-checkpoint decision, pinned
by `test_model_family_table_is_exactly_the_five_families`). Three classical univariate forecasters
plus the two "model over lag features" shapes covers what `coder` (T-029) can plausibly build from a
`design.json`; anything narrower would leave the selector's forecasting route with no defensible
fallback when the temporal signal is thin.

**Alias table: brand tokens discriminate; routing words and the bare lag modifier are not aliased.**
`normalize_model_family` has no longest-match-wins rule (2026-08-12 T-026 discovery) and this task
does not change it, so two modifier axes are handled in the table instead. (1) Seasonal/exogenous
ARIMA spellings — `\barima\b` cannot match inside "sarimax"/"autoarima" (no word break at the seam),
so each variant is listed explicitly; without them "SARIMAX" is a hard abort. (2) The lag-feature
modifier is aliased to **neither** lag family: `gradient_boosting_lags` and `linear_lags` are both
models over lag features, so listing "lag features" under one would make "ridge over lag features"
match two families and raise *ambiguous* on a perfectly clear answer, and listing it under both would
do the same for every phrase. The model brand alone discriminates ("lag features for XGBoost" and
"XGBoost" both resolve via `xgboost`). Generic routing words ("forecast", "time series") are likewise
not aliased: mapping them to a family is an arbitrary modeling decision, and they co-occur with real
family names constantly ("ARIMA for time series forecasting"), which would turn a clear answer into
an ambiguous rejection. The prompt pins the five bare literal tokens instead. Residual risk accepted
and logged: `\bar\b` in the `arima` aliases can make "ridge with ar terms" raise *ambiguous* — a hard
raise is the T-026-sanctioned outcome, strictly better than a silent misclassification.

**CORRECTION (same day, T-027 review) — the claim above that the table had "no cross-family
collisions" was false, and the table has been fixed in three ways.** The blind spot was structural:
the alias tests parametrized over one alias at a time, and a collision by construction needs two
families' vocabulary in one string, so 39 green tests could not see it.
1. *The bare `"linear"` alias is removed.* It is a **trend** word far more often than a family word
   in this domain, and it co-occurs with every other family: "Holt's linear trend method" (the
   textbook name for an `exponential_smoothing` model), "damped linear trend exponential smoothing",
   "ETS with linear trend and additive seasonality", "Prophet (growth=linear)" (Prophet's own
   default) , "Prophet with piecewise linear trend", "ARIMA with linear trend", "SARIMAX with a
   linear time trend", "LightGBM linear_tree", "gradient boosting with linear base learners" — all
   nine raised `ValueError("ambiguous")` and aborted the phase with zero artifacts. Only the
   qualified forms are aliased now: `linear lags`, `linear lag`, `linear regression`, `linear model`.
   The canonical key still round-trips via `"linear lags"`.
2. *The bagging aliases are removed* (`random forest`, `extra trees`, `decision tree`,
   `tree ensemble`, previously pointing at `gradient_boosting_lags`). Demonstrated end to end: a
   coherent RandomForest design (`bootstrap: true`, `oob_score: false`, a bagging `rationale`)
   validated and was written as `model_family: "gradient_boosting_lags"` with its RF
   hyperparameters intact — so `coder` (T-029) would dispatch to a boosting model, receive
   `bootstrap=`/`oob_score=` and die on a constructor `TypeError`, from a `design.json`
   contradicting its own `rationale`. Unaliased, the answer raises "not a supported model family":
   loud, attributable, recoverable. This makes the node consistent with the principle
   `deep_learning_specialist.py:60-73` already states verbatim — rejecting is the safe direction to
   fail. Accepted cost: a genuinely-intended bagged-tree design now aborts instead of degrading.
   That is the correct trade for a value `coder` dispatches on.
3. *Concatenated/CamelCase spellings are listed alongside their spaced twins.* Normalization
   collapses `-`/`_` to a space but never splits CamelCase, so a one-word spelling is unreachable
   from a spaced alias. `ExponentialSmoothing` — statsmodels' **own class name for one of these five
   families** — was a hard abort, as were `HoltWinters`, `GradientBoostingRegressor`,
   `HistGradientBoostingRegressor`, `XGBRegressor`, `LGBMRegressor` and the CamelCase rendering of
   this table's own pinned tokens (`GradientBoostingLags`, `LinearLags`). Follows the convention the
   table already used for `auto arima`/`autoarima`, `fb prophet`/`fbprophet`, `light gbm`/`lightgbm`,
   `elastic net`/`elasticnet`. Note that `"gradientboosting"` alone does **not** fix
   `GradientBoostingRegressor` (`\bgradientboosting\b` has no word break before "regressor"), so the
   `...regressor` forms are listed explicitly rather than relying on the base token.

Guarding the fix: `test_realistic_multiword_phrasings_resolve_to_exactly_one_family` parametrizes
over whole realistic answers rather than bare aliases, which is the only shape that can catch a
collision; `test_bagged_tree_answers_are_rejected_rather_than_resolved_to_boosting` and
`test_bagged_tree_design_does_not_write_a_boosting_family` pin the safe-fail direction. The
docstring on `test_every_production_alias_resolves_to_its_family` now states its real (narrower)
scope instead of claiming collision coverage it structurally cannot have.

**Column identity comes from `feature_spec_ref`; `_FOLD_SUMMARY_KEYS` was deliberately not widened.**
The obvious alternative — carrying a `time_column` through `read_fold_summary` — would change a
shared helper whose output shape three landed sibling prompts (T-024/T-025/T-026) already document,
staling all three. There is also no node-local fold reader here (the redundant copies in
`classical_ml_specialist`/`deep_learning_specialist` are explicitly not replicated). So the node
passes the feature-spec **path** through unchanged and the prompt forbids inventing a
time/date/target column name; `coder` resolves column identity from `feature_spec.json`.

**No self-gate on temporal structure.** The task's "activated only when temporal structure exists" is
satisfied *upstream*: `specialist_selector` (T-023) is the sole gate, and nothing is queued behind
this node for the iteration, so a refusal branch would leave the iteration with zero artifacts. Weak
evidence degrades the design (short-lag `linear_lags`/`gradient_boosting_lags`) and is recorded in
`rationale`. Asserted by `test_node_never_self_gates_on_temporal_signal`, not merely documented.

**"Respect temporal CV / no future leakage" was split into an enforced half and a prompt-level half**
(user-approved reinterpretation). Enforced and tested: `cv_strategy_ref` is pipeline-injected and
never read from the response, and `FORBIDDEN_CV_KEYS` rejects CV redefinition anywhere — including
the `TimeSeriesSplit` argument names (`n_splits`/`test_size`/`shuffle`/`validation`) a temporal
design reaches for most naturally, which is now its own parametrized test. Prompt-level only:
"never uses future data". Leakage is not machine-checkable from `design.json` — `preprocessing` is a
flat token list with no fit-scope notion and `FORBIDDEN_CV_KEYS` matches dict keys, not list values —
so detecting it would mean editing the shared contract T-024–T-028 all inherit. No leakage detection
was added to the validator or the node. Related: the frozen strategy may legitimately not be
time-aware (`stratified_kfold` on a forecasting problem); the folds are write-once, so the prompt
tells the node to design against them and note the mismatch in `rationale` rather than change them.

**Tuple-shaped ARIMA `order`/`seasonal_order` use hyphenated string tokens** (`"1-1-1"`,
`"1-1-1-12"`), either as `categorical` `choices` or pinned in `fixed_params` — never JSON arrays.
Follows T-026's `ngram_range` precedent, and the format is stated in the prompt so it is a public
encoding rather than a private one `coder` has to reverse-engineer.

*Correction (T-027 review) — the enforcement is `choices`-scoped only.* `_validate_choices` does
reject an array inside `search_space` (scalars only), but `_validate_fixed_params` explicitly
permits a **flat list of scalars**, so `fixed_params: {"order": [1, 1, 1]}` is accepted and written
through (verified). The prompt originally justified the ban as a validator rejection, which is false
for the `fixed_params` path; it now states the array ban there as a **pipeline convention** — one
encoding for `coder` to parse instead of two — and `docs/pipeline.md` says the same. The string
convention itself is entirely unvalidated (`"1,1,1"`, `"(1,1,1)"`, `"1-1"`, `"banana"` all pass), so
`coder` (T-029) must parse defensively; recorded in `context/discoveries.md`. The identical
incorrect claim is inherited from `nlp_specialist`'s `ngram_range` section — flagged in discoveries,
deliberately not edited in that landed sibling.
## 2026-08-14 — T-030 [pipeline-agent]
**`code_critic` locates the generated code by well-known workspace path, not by a new `LabState`
field.** `src/state.py` is a protected contract and `experiments: list[dict]` — the only field that
could carry a generated-code pointer — has no writer anywhere in `src/` yet, so adding one would
have meant a protected-contract change plus a writer in a node this task does not own. The node
therefore reads `experiments/exp_{current_iteration}/train.py` (plus `design.json`/`results.json`
from the same directory), exactly the precedent the Phase 5 specialists set for `design.json`. When
`state["experiments"][-1]["path"]` *is* usable it is preferred, so the node keeps working unchanged
once `coder` (T-029) starts recording it; because T-029 has not fixed whether that value is the
directory or `results.json` inside it, a value carrying a suffix is treated as a file pointer and
its parent is used. Absolute values are re-relativized; `..`, out-of-workspace and non-string values
fall back to the well-known directory rather than raising.

**Retry budget from the phase YAML, not `Settings`.** `max_retries` comes from
`load_phase_config("phase5_implementation").critic.max_retries`, not
`Settings.execution.max_critic_retries`. Both are `3` today, but the phase YAML is the contract that
*also* names `code_critic` as the phase's critic and `[coder]` as its targets — reading the budget
from anywhere else would let the two drift — and it allows a per-phase budget. The unit test reads
the same value from the same loader rather than hardcoding `3`.

**`resolve_node` bound through the `node_resolver` module attribute.** `from src.graph import
node_resolver` + `node_resolver.resolve_node(target)` at call time, matching
`src/graph/builder.py:70-73` — deliberately *not* `analysis_critic`'s import-time
`from src.graph.node_resolver import resolve_node`, which B-001 proved import-order fragile
(a `monkeypatch.setattr` on the resolver module silently fails to reach it once the node module is
imported). Consequence recorded in the module docstring: unit tests patch
`src.graph.node_resolver.resolve_node`, and `src.nodes.llm.code_critic.resolve_node` deliberately
does not exist, so `tests/unit/graph/test_checkpointer.py::_install_counting_resolver` needs no
third binder for this node.

**JSON extraction and degrade errors reused from `_experiment_design.py`, not re-copied.**
`extract_json_object`, `DEGRADE_ERRORS` and `read_fold_summary` are imported rather than
reimplemented — an eighth private fence-stripper copy was explicitly rejected by the Orchestrator
(see the open T-024 entry in `context/discoveries.md`). `extract_json_object`'s `specialist`
parameter is an error-attribution label only, so passing `"code_critic"` is correct usage.

**`extract_json_object` is not sufficient on its own, and the reason is specific to a code critic**
(corrected in review; an earlier version of this entry claimed it "subsumes both cases
`analysis_critic._fence_candidates` was built for", which measurement disproved). It does handle a
fenced whole response and it salvages the first-`{`-to-last-`}` window past a **brace-free** trailing
fenced block. But when the trailing block *contains braces* the salvage window runs from the real
object's `{` to the snippet's `}` and fails — and for this node the single most likely postamble is a
Python snippet illustrating the fix, which is full of braces. Measured consequence: the response was
discarded whole, so an `iterate` lost its `feedback` to the "could not parse" text (re-invoking
`coder` with no signal and burning one of three retries) and a `pass` became a spurious `iterate`
(a wasted regeneration cycle). `code_critic._extract_verdict_data` therefore retries on the prefix
before each fence marker. For the record, `analysis_critic`'s `_fence_candidates` does **not** handle
these shapes either — measured, it returns `None` for both the brace-free and the brace-bearing
trailing-fence response, because it only builds extra candidates when the response *starts* with a
fence. Neither implementation dominates the other, which is a further argument for the shared-helper
hoist proposed in `context/discoveries.md`.

**`_parse_verdict` catches `DEGRADE_ERRORS`, not a bare `ValueError`** (review fix). `json.loads`
raises **`RecursionError`** — not a `ValueError` subclass — on a deeply nested payload, reproducible
at ~2 400 characters and therefore reachable within this agent's `max_tokens`. The bare
`except ValueError` let it escape, which would have aborted the whole graph run from the one node
whose entire contract is to degrade, and aborted it *before* any verdict record was written. This was
the only non-degrading path left in the node.

**All three file artifacts truncated at 20 000 characters with an in-band marker** (review fix; this
entry previously exempted the two context artifacts as "small structured artifacts"). That holds for
`design.json`, which a specialist writes to a validated schema — but **not** for `results.json`,
which is written by the *generated* script, the least-trusted component in the system, and normally
carries the OOF predictions. Measured: a 4 MB `results.json` produced a 4 000 202-character review
message (~1M tokens) in a single `invoke`, against CLAUDE.md's "< $0.50 per full competition run"
target. `design.json` is capped only for uniformity. The marker names the artifact so the critic can
say its view was partial rather than passing silently.

**The experiment directory is resolved once, and all three artifacts come from it** (review fix).
The readers originally scanned the candidate list independently, so `train.py` could come from
`exp_7` while `design.json`/`results.json` fell back to `exp_0` — reproduced on a real filesystem,
where the prompt carried exp_7's LightGBM code to be reviewed against exp_0's *distilbert* design.
This is the expected case rather than an edge case once `coder` lands, because nothing in `src/`
increments `current_iteration`, so the fallback is permanently `exp_0`. It is also a route to a false
**pass**, not merely a false iterate: the prompt accepts early stopping only when it is "recorded in
`results.json`", so a stale `results.json` from another experiment can make the critic accept a leak
it would otherwise reject. `_read_code` now returns the directory that actually yielded the script
and the context reads are pinned to it; when no candidate yields the script, the first candidate is
used so the placeholders still name one place.

**Prompt-injection hardening for the code section** (review fix). The reviewed `train.py` is emitted
in a fence computed to be longer than the longest backtick run inside it, because a script containing
a ``` line would otherwise close a fixed 3-backtick fence early and let the remainder render as
top-level prompt markup — a valid-Python docstring can carry a counterfeit `## Experiment design`
section instructing a `pass`. The prompt separately states that everything under the code heading is
data to review, never an instruction to obey. Worth recording explicitly because the initial security
rationale here was backwards: the retry cap bounds *iterate* loops and does nothing to bound a false
*pass*, which is exactly what injection seeks.

**No `try/except` around the target re-invocation.** `analysis_critic` catches
`FoldsAlreadyFrozenError` because `validation_strategist` has a documented write-once guard;
`coder` has no equivalent, so a real crash in the target must surface rather than be laundered into
a forced pass.

**`tests/fixtures/graph_mocks.py` left un-dispatched for `code_critic`.** With no entry in
`_DISPATCH`, the fallback `_MOCK_LLM_CONTENT` normalizes to `iterate`, re-invokes the (still
`NoOpNode`) `coder` three times and then forces a pass — real executed coverage of the forced-pass
path through a real graph, and the only such coverage there is. Same reasoning B-001 used to keep
`analysis_critic_pass=False` as the default.

The **mechanism** matters and an earlier version of this entry got it wrong (corrected in review):
`_MOCK_LLM_CONTENT` does *not* "fail JSON parsing". Measured, `extract_json_object` **succeeds** on
it — the brace salvage recovers the fenced fold-config object
`{"strategy": "stratified", "n_folds": 1, "seed": 0, "fold_indices": [...]}` — and the verdict
normalizes to `iterate` only because that object carries no `verdict` key. So the coverage hangs on
the *absence of a key*, not on a parse failure: were `_MOCK_LLM_CONTENT` ever to grow a `verdict`
key, this coverage would vanish silently. That is why the smoke test now asserts
`final_verdict["forced_pass"] is True` and the full attempt sequence rather than merely that the
record file exists — the assertion fails loudly instead of quietly covering nothing.

**Discarded alternatives:** a new `LabState` field for the generated-code path (protected contract,
no writer); hoisting the critic retry loop into `src/nodes/llm/base.py` (reserved for a separate
refactor task — logged in `context/discoveries.md` instead); a `{phase}` placeholder in
`output_file_pattern` (this node runs in exactly one phase, so `{iteration}` alone disambiguates).

## 2026-08-17 — T-028 [pipeline-agent]
**`ENSEMBLE_DESIGN_KEYS`/`validate_ensemble_design` is a thin wrapper, not a parameter widening
`validate_experiment_design`.** `ensemble_specialist` is the only Phase 5 specialist whose
`design.json` needs a ninth key (`base_experiments`), so the alternative — adding a keyword-only
`base_experiments: Any | None = None` parameter to `validate_experiment_design` itself — was
rejected: it would force the four landed siblings (`classical_ml_specialist`,
`deep_learning_specialist`, `nlp_specialist`, `timeseries_specialist`) to keep *not* passing a
parameter that means nothing to them, and it would put `DESIGN_KEYS`'s frozen
`assert tuple(result) == DESIGN_KEYS` test one accidental default-value change away from breaking.
Instead `ENSEMBLE_DESIGN_KEYS = DESIGN_KEYS + ("base_experiments",)` and
`validate_ensemble_design(...)` calls `validate_experiment_design(...)` unchanged, then appends a
whitelist-rebuilt `base_experiments` as the last key. `DESIGN_KEYS`,
`validate_experiment_design`'s signature, and `tests/unit/nodes/llm/test_experiment_design.py`'s
`assert tuple(result) == DESIGN_KEYS` are all byte-for-byte untouched — verified by running that
exact test unmodified before touching anything else in `_experiment_design.py` (implementation
order 1 in the task plan). `_validate_base_experiments` requires a **non-empty** list, mirroring
`_validate_search_space`'s "must not be empty" floor: an ensemble over zero sources is
unrepresentable. It deliberately requires only `>= 1`, not `>= 2` — the `>= 2` eligibility rule is
`specialist_selector._should_ensemble`'s routing decision (already made before this node ever
runs), not this shared schema's to re-derive; a single-source ensemble is degenerate but
representable, and `test_exactly_one_experiment_still_writes` pins that boundary rather than
merely documenting it.

**Alias table: three canonical families, with a bare-modifier defusing technique borrowed from
`timeseries_specialist`'s `"linear"` precedent — and one case it cannot defuse.**
`normalize_model_family` still has no longest-match-wins rule (2026-08-12 T-026 discovery,
unchanged by this task), so `ensemble_specialist`'s own `_MODEL_FAMILIES` table
(`stacking`/`blending`/`weighted_average`) deliberately carries no bare `"weighted"`, `"weight"`,
`"stack"` or `"stacked"` alias — only qualified multi-word forms (`"weighted blend"`, `"weighted
average"`, `"stacked ensemble"`, ...). This defuses the realistic phrasing "weighted blend of
stacked models" to `blending` alone (verified: `"blending"`'s "weighted blend" alias matches;
"stacked" matches nothing, so `stacking` never enters the candidate set) — pinned by
`test_weighted_blend_of_stacked_models_resolves_to_blending`. It structurally **cannot** defuse
`"blended stacking"`: "blended" is `blending`'s own self-match alias and "stacking" is
`stacking`'s own self-match alias, so any phrase containing both words matches two families no
matter how the table is tuned — removing either alias would make the bare canonical token
unreachable for its own family, which is worse. This is accepted as structurally ambiguous by
design, not a bug: `test_ambiguous_multiword_phrasings_raise` parametrizes over `"blended
stacking"`, `"stacking and blending combined"` and `"a meta learner that blends the outputs"`, all
of which raise `ValueError("ambiguous")` rather than silently resolving to one family. The
prompt's `## model_family — exactly one of three literal tokens` section states the rejection
explicitly with these same examples and tells the LLM to put the nuance in `rationale` instead —
same push-the-ambiguity-into-prose approach the T-026 discovery entry recommends for a table with
no precedence rule.

**`_experiment_dir_from_entry` is a local duplication of `code_critic._experiment_dir_from_state`'s
normalization, not an import.** Both functions relativize a `state`-recorded path, treat a
suffixed value as a file pointer (take its parent), and reject a `..` component — but
`code_critic`'s version operates on `state["experiments"][-1]` (the *last* entry only, for its own
single-experiment review), while `ensemble_specialist` needs the same normalization applied to
*every* entry independently (`_build_base_experiments` calls it once per entry, by index). Sibling
LLM node modules never import from each other — established at T-022/T-024/T-025 and restated in
`code_critic`'s own module docstring — so this task duplicates the four-step normalization locally
(`src/nodes/llm/ensemble_specialist.py:_experiment_dir_from_entry`) rather than importing from
`code_critic.py` or hoisting a shared helper, consistent with that precedent. One deliberate
divergence: the fallback directory uses the entry's own recorded `iteration` field when it is a
real (non-bool) int, falling back to the entry's list position only when that field is missing or
the wrong type (`_fallback_iteration`) — `code_critic`'s single-entry version has no need for this
because it only ever has one candidate entry to fall back for.

**Per-source weight parameters are named by positional index, never by `experiment_id`.**
`base_experiments[i]["experiment_id"]` is free text carried over from `state["experiments"]`'s own
`id` field (or a synthesized `experiment_{i}` fallback) and is not guaranteed to be a valid Python
identifier — `exp-3`, `exp 07`, and similar are all plausible values `coder`-adjacent tooling might
produce, and `_PARAM_NAME_RE` requires `^[A-Za-z_][A-Za-z0-9_]{0,63}$`. Using the entry's
*positional index* in `## Base experiments` instead (`weight_0`, `weight_1`, ...) sidesteps that
entirely: the index is always available, always a valid identifier suffix, and is the same value
`_render_base_experiments` already lists the entries in order by — so the prompt's instruction
("use the positional index, never the raw `experiment_id`") points at something the LLM can read
directly off the injected section rather than having to invent a sanitization scheme of its own.
`test_weighted_average_design_with_index_named_weights_accepted` pins the shape as a passing
example; the prompt's own worked JSON example intentionally uses a `stacking` design instead (a
meta-learner's own hyperparameter, `alpha`, needs no per-source naming scheme at all), so the two
together cover both `model_family` branches' distinct parameter-naming needs.

## 2026-08-18 — T-028 [pipeline-agent]
**Fallback-numbering collision (adversarial review BLOCKER): unified `_experiment_id`'s fallback
onto `_fallback_iteration`, and added a duplicate-`oof_path` rejection to
`_validate_base_experiments` as a genuine schema invariant.** The initial T-028 landing (above) had
two independent fallback-numbering sources for the same degraded `state["experiments"]` entry:
`_experiment_id`'s fallback used the entry's raw list **index** (`experiment_{index}`), while
`_experiment_dir_from_entry`'s fallback (via `_fallback_iteration`) preferred the entry's own
recorded `iteration`, falling back to the index only when `iteration` was absent or the wrong type.
For `[{"iteration": 1}, {}]` this produced `experiment_0`/`experiment_1` — two distinct ids — both
pointing at directory `experiments/exp_1` (the first via its own `iteration`, the second via its
list position, which happens to equal `1`), and therefore identical `oof_path`s. Nothing rejected
the collision: `_validate_base_experiments` checked shape only, so `design.json` would have
shipped two rows under two labels reading the same OOF file, and `coder` (T-029) would fit a
meta-learner double-counting one real source while silently never reading whichever source the
colliding entry actually meant — corruption with no error anywhere in the pipeline.

Two defensible fixes were on the table: (a) make `_experiment_id`'s fallback derive from
`_fallback_iteration(entry, index)` too, so a degraded entry's id and directory always agree, or
(b) drop the `iteration` preference entirely and number both purely by list position. **(a) was
chosen** — it is the smaller change (one call site), and it preserves `_fallback_iteration`'s
original rationale intact: preferring the entry's own recorded `iteration` keeps the fallback
*directory* pointing at that experiment's real location when entries are read out of order or
interleaved with entries missing a `path`, and unifying the id onto the same value extends that
same correctness property to the id rather than discarding it. Both `_fallback_iteration`'s and
`_experiment_id`'s docstrings were rewritten to state the unified rule explicitly (the old
`_fallback_iteration` docstring justified the divergence by describing itself as "the value used to
build the fallback experiment **directory**" only — that framing no longer holds now that
`_experiment_id` derives from it too).

Unifying the numbering does not, by itself, make a coincidental collision impossible — two entries
can still each carry their own real, distinct-looking `iteration` values that happen to be equal
(or one entry's own `iteration` happens to equal another entry's list position), and now that both
functions share one source, such entries collide on **both** id and directory rather than drifting
apart. So `_validate_base_experiments` (`src/nodes/llm/_experiment_design.py`) now tracks
`oof_path`s seen so far and raises `ValueError` naming both colliding `experiment_id`s and the
shared `oof_path` the moment a second entry resolves to a path already claimed — in the same
"internal error" phrasing style the function already uses for its other pipeline-injected-data
violations. This is the correct behavior, not a regression: two `state["experiments"]` entries that
resolve to the same OOF source are a genuinely ambiguous, unrepresentable ensemble design, and
failing loudly at design-write time beats writing a `design.json` that silently double-counts one
source. Two entries with real, distinct fallback numbers (e.g. `{"iteration": 1}` and
`{"iteration": 2}`) still produce distinct `oof_path`s and write successfully —
`test_two_legitimately_distinct_degraded_entries_write_successfully` pins that the rejection
targets the collision specifically, not degraded entries in general.

Test coverage added: the exact reproducer (two degraded entries that previously collided) now
raising with nothing written
(`test_two_degraded_entries_with_colliding_fallback_numbers_raise_and_write_nothing`); a direct
unit-level test of `_validate_base_experiments` rejecting a duplicate `oof_path`
(`test_validate_base_experiments_rejects_duplicate_oof_path_directly`) and accepting distinct ones
(`test_validate_base_experiments_accepts_distinct_oof_paths_directly`); two legitimately distinct
degraded entries still writing successfully
(`test_two_legitimately_distinct_degraded_entries_write_successfully`); and the unified-numbering
rule itself, pinning that a degraded entry's fallback id and the directory used to resolve its OOF
path agree (`test_degraded_entry_fallback_id_and_directory_agree`) — all in
`tests/unit/nodes/llm/test_ensemble_specialist.py` and
`tests/unit/nodes/llm/test_experiment_design.py`.

Separately (adversarial review Finding 3, low severity): widened the prompt's § model_family
rejected-examples list (`config/prompts/ensemble_specialist/v1.md`) with five ambiguous phrasings
the adversarial reviewer found plausible and unwarned-against (`"super learner blend"`, `"weighted
average of blends"`, `"convex combination blend"`, `"holdout blend with learned weights"`, `"a
stacked super learner with weighted blend"`), each verified against the real `_MODEL_FAMILIES`
table to actually raise `ValueError("ambiguous", ...)` before being added, and pinned by extending
`test_ambiguous_multiword_phrasings_raise`'s parametrization. `_MODEL_FAMILIES` and the no-retry
behavior on this node were left unchanged — both remain accepted, documented design decisions, out
of scope for this fix.
