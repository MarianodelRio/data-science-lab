---
id: T-041
phase: 4
agent: frontend-agent
depends_on: [T-038]
status: available
folders: ["frontend/"]
outputs: [Chat component over WebSocket]
size: M
branch: ~
pr: ~
---

## Chat component (frontend/)

**Scope:** `frontend/src/components/Chat/`.

**Delivers:**
- WebSocket client to `WS /api/runs/{id}/chat`
- Message list + input; streams explainer responses
- Auto-focuses and surfaces the checkpoint summary at interrupts, with approve / redirect actions that call `resume`
  (both buttons call the same forward `resume` — "redirect" only changes the feedback text sent, it does not undo or re-run the completed phase)

**Done when:**
- [ ] sending a message over a mocked WebSocket appends it and renders the streamed reply (component test)
- [ ] an interrupt surfaces the checkpoint summary with approve/redirect buttons (both call `resume`; redirect only differs in feedback text)
- [ ] approve sends the resume payload over the socket (asserted with mock)
- [ ] socket drop shows a reconnecting indicator
- [ ] `npm run lint` + component tests pass
- [ ] `docs/api.md` chat protocol note updated
