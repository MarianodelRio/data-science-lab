---
id: T-040
phase: 4
agent: frontend-agent
depends_on: [T-038]
status: available
folders: ["frontend/"]
outputs: [ExperimentsTable component]
size: S
branch: ~
pr: ~
---

## ExperimentsTable component (frontend/)

**Scope:** `frontend/src/components/ExperimentsTable/`.

**Delivers:**
- Table of experiments per iteration: model, CV score, delta vs baseline, best highlighted
- Baseline row pinned as the permanent reference
- Sortable by score/iteration

**Done when:**
- [ ] given a fixture list of experiments, the table renders one row per experiment plus the baseline row
- [ ] the best experiment is visually marked
- [ ] delta vs baseline is computed and displayed with correct sign
- [ ] sorting by score reorders rows (component test)
- [ ] `npm run lint` + component tests pass
- [ ] `docs/api.md` experiments payload note updated
