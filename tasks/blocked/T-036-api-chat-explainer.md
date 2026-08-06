---
id: T-036
phase: 3
agent: api-agent
depends_on: [T-034, T-010]
status: blocked
folders: ["src/api/"]
outputs: [WS /api/runs/{id}/chat, explainer agent subgraph]
size: M
branch: ~
pr: ~
---

## WebSocket chat + explainer agent (src/api/)

**Scope:** `src/api/routers/chat.py` + `src/api/explainer.py`.

**Delivers:**
- `explainer` agent: a small LangGraph subgraph / LLM node with **read-only** access to LabState, workspace files (via `WorkspaceManager`), and RagStore; answers questions and relays interrupt decisions. `model_role: reasoning`
- `WS /api/runs/{id}/chat` — bidirectional WebSocket; user messages → explainer, responses streamed back
- At an interrupt, the explainer surfaces the checkpoint summary and forwards the user's approve/redirect into `resume`
  (same forward call in both cases — "redirect" is corrective feedback text, not a different code path; it does not re-run the completed phase)

**Done when:**
- [ ] a WebSocket client sends a question and receives an explainer answer (mock LLM)
- [ ] the explainer reads workspace files through `WorkspaceManager` (asserted) and never writes
- [ ] an approve message during an interrupt triggers a resume call (asserted via fake graph)
- [ ] malformed message returns an error frame, socket stays open
- [ ] tests use the `TestClient` WebSocket + mocks, no network
- [ ] `docs/api.md` documents the chat protocol
