---
id: T-045
phase: 5
agent: pipeline-agent
depends_on: []
status: in-progress
folders: ["docs/", "README.md"]
outputs: [docs/pipeline.md, docs/agents.md, docs/configuration.md, docs/api.md, README sections]
size: S
branch: feature/T-045-docs-skeletons
pr: ~
---

## Docs skeletons (docs/)

**Scope:** `docs/` skeletons that later tasks fill in. Primary doc: `docs/pipeline.md`.

**Delivers:**
- `docs/pipeline.md` — headings for: State, Graph topology, The 7 phases, Node classification, Tools, RAG, Observability, Invariants
- `docs/agents.md` — table header (agent · pipeline phase · model_role · output file) + "Adding an agent" section
- `docs/configuration.md` — settings.yaml schema, "Changing a model", "Adding/removing an agent", prompt versioning
- `docs/api.md` — endpoint reference skeleton (REST + SSE + WebSocket)
- README: fill "What is this" (use the approved public description) + link to docs

**Done when:**
- [ ] all four docs exist with the section headings above
- [ ] `docs/agents.md` has the table header ready for node tasks to append rows
- [ ] README links to each doc and contains the project description
- [ ] no broken relative links (all referenced files exist or are created here)
- [ ] markdown lints clean if a linter is configured
