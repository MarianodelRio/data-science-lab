---
id: T-002
phase: 0
agent: infra-agent
depends_on: [T-001]
status: available
folders: ["src/"]
outputs: [LabState TypedDict, state helper functions]
size: S
branch: ~
pr: ~
---

## LabState contract (src/state.py)

**Scope:** `src/state.py` only. This is a **shared contract** — every node reads/writes it.

**Delivers:**
- `LabState(TypedDict)` exactly as defined in `design.md` § Shared contracts (all fields, correct types, `messages: Annotated[list, add_messages]`)
- `new_state(competition_name, workspace_path) -> LabState` factory with sensible defaults (`current_iteration=0`, `iterations_without_improvement=0`, `best_score=-inf`, empty paths/lists)
- Type-only module: no I/O, no LLM, no side effects

**Done when:**
- [ ] `LabState` contains every field listed in `design.md` with matching types
- [ ] `new_state("x", "/tmp/x")` returns a dict where `current_iteration == 0` and `experiments == []`
- [ ] `mypy src/state.py` passes with no errors
- [ ] unit test asserts every required key is present after `new_state()`
- [ ] `docs/pipeline.md` "State" section updated
