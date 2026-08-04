---
id: T-045
phase: 5
agent: pipeline-agent
depends_on: []
status: done
folders: ["docs/", "README.md"]
outputs: [docs/pipeline.md, docs/agents.md, docs/configuration.md, docs/api.md, README sections]
size: S
branch: feature/T-045-docs-skeletons
pr: "https://github.com/MarianodelRio/data-science-lab/pull/3"
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

## Completed

Created all four skeleton docs per the approved plan, each with the exact heading order and
one `> Skeleton — …` blockquote per section:

- `docs/pipeline.md` — State, Graph topology, The 7 phases, Node classification, Tools, RAG,
  Observability, Invariants
- `docs/agents.md` — `## Agents` section with the table header row
  `| Agent | Pipeline phase | model_role | Output file |` (ready for future tasks to append
  rows) and `## Adding an agent` skeleton section
- `docs/configuration.md` — settings.yaml schema, Changing a model, Adding/removing an agent,
  Prompt versioning
- `docs/api.md` — REST, SSE, WebSocket skeleton sections

`README.md` did not exist in the worktree at task start (verified `ls README.md` returned no
such file — T-001's project scaffold hasn't merged this yet), so it was created fresh with the
`# Data Science Lab` title, the `## What is this` project description, and a `## Documentation`
section linking to all four docs.

No markdown linter is configured in this repo: checked for `.markdownlint.json`,
`.markdownlint.yml`/`.yaml`, and any lint step in CI config — none found. Manually verified: no
trailing whitespace, a blank line before/after every heading, consistent `-` bullet style, and
that all four `docs/*.md` relative links in `README.md` resolve to files that exist.

Note: this worktree's feature branch was cut from `main` before the orchestrator's claim commit
for T-045 landed, so `tasks/in-progress/T-045-docs-skeletons.md` was initially absent here. Fast-
forward-merged `origin/main` (2 metadata-only commits: T-001 and T-038 claims, plus this task's
own claim) into the feature branch to pick it up before appending this section — no conflicts,
no code changes involved.

**Post-review fix (adversarial pass):** the `docs/agents.md` table skeleton originally had an
HTML comment on its own line directly after the header/separator row. Adversarial review found
this terminates GFM table parsing (verified with pandoc) — any row a future task appends after
that comment would render as broken text, not a table row, silently breaking the table for all
21 future node-adding tasks. Fixed by moving the "append rows here" instruction to plain text
above the table. Also logged an `OPEN` entry in `context/discoveries.md` flagging the expected
`README.md` merge overlap with T-001 (both create it independently — project description/doc
links vs. setup steps; reconcile by keeping both sections on merge).

**Post-merge conflict resolution:** after T-001 and T-038 merged to main, rebased and resolved 3
conflicts — `README.md` (kept T-001's setup/dev-commands content, added T-045's "Documentation"
links section after "Architecture"), `docs/api.md` (T-038 had pre-populated it with a real
endpoint table + "Frontend client" section per its own discovery note asking T-045 to reconcile
rather than overwrite; restructured T-038's entries into the REST/SSE/WebSocket headings and
preserved the "Frontend client" section), and `context/discoveries.md` (append-only, kept all
entries, marked the two this merge resolved as `resolved in T-045`). All links re-verified to
resolve post-merge. See PR #3 comment for the full breakdown.
