---
id: T-011
phase: 1
agent: pipeline-agent
depends_on: [T-002]
status: pr-open
folders: ["src/nodes/"]
outputs: [ComputeNode base class, node lifecycle without LLM]
size: S
branch: feature/T-011-compute-node-base
pr: "https://github.com/MarianodelRio/data-science-lab/pull/12"
---

## Compute node base (src/nodes/compute/base.py)

**Scope:** `src/nodes/compute/base.py` only.

**Delivers:**
- `ComputeNode` base class: `__call__(state: LabState) -> dict` with a `run(state)` hook subclasses implement — pure Python, no LLM
- Access to `WorkspaceManager` for reading/writing files
- Same convention as LLM nodes: `src/nodes/compute/{name}.py` exposing a class whose `name` matches (GraphBuilder imports by name)

**Done when:**
- [x] a sample subclass reads a state field and returns a computed state delta
- [x] base class does not import any LLM module
- [x] `mypy src/nodes/compute/base.py` passes
- [x] unit test covers the run→delta lifecycle
- [x] `docs/pipeline.md` "Node classification" section updated

## Completed

Implemented `ComputeNode` (`src/nodes/compute/base.py`), per PR #12.

- `ComputeNode(ABC)`: no-arg constructible, plain `name: str` class-attribute annotation
  (never itself set, so it can never accidentally satisfy `resolve_node`'s lookup), abstract
  `run(state) -> dict` hook, `__call__(state) -> dict` delegating to `run`. Matches
  `src/graph/node_resolver.py`'s discovery contract exactly (T-009).
- `ComputeNode.workspace(state)` builds a fresh `WorkspaceManager(state["workspace_path"])`
  per call rather than caching one at `__init__` — required since `resolve_node` instantiates
  nodes via `cls()` with no arguments.
- `tests/unit/nodes/compute/test_base.py` (new `tests/unit/nodes/` subtree): 5 tests using a
  local `_DoubleIterationNode` fixture — run→delta, `__call__`→`run` delegation, actual
  filesystem write-through via `WorkspaceManager` (not mocked), ABC abstractness
  (`TypeError` on direct instantiation), and a static AST-based check that the module never
  imports `src.llm`/`langchain*` (avoids false negatives from import side effects elsewhere
  in the suite).
- `docs/pipeline.md` "Node classification": added `### ComputeNode base class` subsection.
- Fixed a pre-existing canary test that started failing as an expected consequence of landing
  the first real module under `src/nodes/`: `tests/unit/graph/test_builder.py::test_no_real_node_modules_exist_yet`
  now excludes the `"base"` filename stem (infrastructure, never itself a resolvable node
  name) while still failing for any genuine future concrete node module. Verified adversarially
  that this exclusion carries no real foot-gun — a node literally named `base` would collide
  with the file at the filesystem level regardless, and would fail loudly via `GraphBuilderError`
  (0 matches) even if it somehow existed.
- Full review round (code-quality, security, adversarial, smoke-tester; mutation-tester skipped —
  module not in `devteam.config.yml`'s `critical_modules`) found zero blockers/warnings; 3
  non-blocking nits noted for awareness only (base.py's docstring describes T-010's
  not-yet-landed `LLMNode` shape and could go stale when T-010 lands; the abstract `run`'s
  `raise NotImplementedError` body is unreachable/uncovered, which is standard for ABCs; no
  explicit test asserts `ComputeNode` itself has no `name` attribute, though it's
  double-protected by `node_resolver`'s `obj.__module__ == module.__name__` check).
