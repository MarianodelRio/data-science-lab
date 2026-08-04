---
id: T-008
phase: 1
agent: infra-agent
depends_on: [T-001]
status: available
folders: ["src/tools/", "src/memory/"]
outputs: [RagStore.index, .query with metadata filter, local embeddings, structured extraction schema]
size: M
branch: ~
pr: ~
---

## rag tool + Chroma + embeddings (src/tools/rag.py, src/memory/store.py)

**Scope:** `src/tools/rag.py` + `src/memory/store.py`.

**Delivers:**
- `RagStore(competition_name, chroma_host, chroma_port)` — one Chroma collection per competition
- Local embeddings via `sentence-transformers` (`nomic-embed-text` or `all-MiniLM-L6-v2`), no external API
- `index(documents: list[Document])` where each Document carries the structured metadata from `design.md` § RAG (`source, problem_type[], methods_used[], dataset_characteristics[], key_findings, relevance_score`)
- `query(text, where: dict | None, n_results) -> list[Document]` supporting metadata filters
- `IndexDocument` dataclass defining the metadata schema (the structured-extraction contract used by research nodes)

**Done when:**
- [ ] `index()` then `query("xgboost imbalance")` returns the indexed doc (against a local/ephemeral Chroma client)
- [ ] `query(..., where={"problem_type": {"$in": ["binary_classification"]}})` filters correctly
- [ ] embeddings run locally with no network call
- [ ] two different `competition_name` values use isolated collections
- [ ] `mypy src/tools/rag.py src/memory/store.py` passes
- [ ] tests cover: index+query, metadata filter, collection isolation
- [ ] `docs/pipeline.md` "RAG" section updated
