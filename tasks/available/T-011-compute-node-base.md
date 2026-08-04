---
id: T-011
phase: 1
agent: pipeline-agent
depends_on: [T-002]
status: available
folders: ["src/nodes/"]
outputs: [ComputeNode base class, node lifecycle without LLM]
size: S
branch: ~
pr: ~
---

## Compute node base (src/nodes/compute/base.py)

**Scope:** `src/nodes/compute/base.py` only.

**Delivers:**
- `ComputeNode` base class: `__call__(state: LabState) -> dict` with a `run(state)` hook subclasses implement — pure Python, no LLM
- Access to `WorkspaceManager` for reading/writing files
- Same convention as LLM nodes: `src/nodes/compute/{name}.py` exposing a class whose `name` matches (GraphBuilder imports by name)

**Done when:**
- [ ] a sample subclass reads a state field and returns a computed state delta
- [ ] base class does not import any LLM module
- [ ] `mypy src/nodes/compute/base.py` passes
- [ ] unit test covers the run→delta lifecycle
- [ ] `docs/pipeline.md` "Node classification" section updated
