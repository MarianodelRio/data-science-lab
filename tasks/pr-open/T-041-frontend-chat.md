---
id: T-041
phase: 4
agent: frontend-agent
depends_on: [T-038]
status: pr-open
folders: ["frontend/"]
outputs: [Chat component over WebSocket]
size: M
branch: feature/T-041-frontend-chat
pr: https://github.com/MarianodelRio/data-science-lab/pull/46
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

## Completed

- Implemented per the Architect's MODIFIED scope (flat `frontend/src/components/Chat.tsx` +
  `Chat.test.tsx`, no `Chat/` subdirectory; `frontend/README.md` gains the Chat protocol section
  instead of `docs/api.md`, which is api-agent's and already documents the WS protocol via
  T-036).
- `frontend/src/api/types.ts` — replaced the provisional `ChatMessage` interface with
  wire-accurate discriminated unions `ChatClientFrame` (`question`/`approve`/`redirect`) and
  `ChatServerFrame` (`checkpoint`/`answer`/`resumed`/`error`), reconciled against
  `docs/api.md` § WebSocket / `src/api/routers/chat.py`. Updated the file's top-of-file
  PROVISIONAL doc-comment accordingly; left `CreateRunPayload`/`ResumePayload`/
  `SubmitResponse`/`MlflowOpenResponse`/`Experiment` untouched (out of scope).
- `frontend/src/api/client.ts` — retyped `ChatConnection` to use the new frame types and
  rewrote `connectChat`'s body to send/receive frames instead of the old `{content}` shape.
- `frontend/src/api/client.test.ts` — fixed the two tests that asserted the old `ChatMessage`/
  `{content}` shape, added coverage for all three `ChatClientFrame` variants and an
  `onOpen`-forwarding test; confirmed via grep that no `ChatMessage`/`content:` references
  remain.
- `frontend/src/components/Chat.tsx` — full rewrite: opens `WS /api/runs/{id}/chat` via
  `connectChat` when given a `runId`, renders message history, gates approve/redirect controls
  on having received a `checkpoint` frame (not on its fields being non-empty), reconnects on an
  unintentional close with a fixed backoff schedule capped at 5 attempts, and preserves message
  history across a same-`runId` reconnect while clearing it on a `runId` change.
- `frontend/src/components/Chat.test.tsx` — new component test suite (17 tests) covering: no-run
  empty state, opening a connection, send/receive of `question`/`answer` frames, disabled Send
  on blank input, checkpoint-gated approve/redirect controls (including the `""`/`""` regression
  case), approve/redirect frame payloads, `resumed` clearing pending state, `error` frames
  rendering and un-disabling controls, checkpoint dedupe, feedback-textarea autofocus, the
  immediate "Reconnecting…" banner, the full 5-attempt backoff schedule with history preserved
  and exactly 6 total `connectChat` calls, the terminal "Unable to reconnect" state after the
  6th failed attempt, and history/connection reset on a `runId` change.
- `frontend/README.md` — appended the "Chat: WebSocket protocol, reconnect, and history
  ownership" section per the plan, documenting the frame protocol, reconnect backoff, and that
  chat history is frontend-owned across reconnects.
- `frontend/src/components/Layout.test.tsx` — minor gap not covered by the plan: three existing
  assertions still expected the old placeholder text ("Chat is not implemented yet.") in the
  Chat tab panel. Since `Layout.tsx` itself is untouched (out of scope) and still renders
  `<Chat />` propless, its panel now legitimately shows Chat's real no-run empty state ("No run
  selected."). Updated the three stale assertions to match, using the same grep-driven
  verification approach the plan specified for `client.test.ts` (confirmed no remaining
  `chat is not implemented` references).

### Deviations from plan
- None on `Chat.tsx`, `client.ts`, `types.ts`, or `Chat.test.tsx` — implemented exactly as
  specified, including the `onOpen` extension to `ChatConnection`, the `MAX_RECONNECT_ATTEMPTS`/
  `RECONNECT_DELAYS_MS` constants, and the render-time `prevRunId` reset idiom.
- One addition beyond the plan's file list: `frontend/src/components/Layout.test.tsx` (see
  above) — a minor, plan-adjacent gap resolved with the simplest correct fix rather than a
  blocker, since it only updates stale assertions to match Chat's new real behavior and touches
  no production code.

### Key decisions
- `onOpen` added to the `ChatConnection` interface, as specified by the Planner: native
  `WebSocket.send()` throws `InvalidStateError` before the handshake completes
  (`readyState !== OPEN`), and unlike `PipelineView`/SSE (receive-only), `Chat` sends over the
  socket, so it needs a real open confirmation to gate `send`-enabled UI on. `client.ts` remains
  the sole backend contact point; this extends its contract rather than bypassing it.
- Checkpoint dedupe compares `{phase, summary}` against the last *appended* checkpoint only
  (`lastCheckpointRef`), cleared on `resumed` — a fresh interrupt after a prior one was resolved
  appends normally, only a re-announced identical checkpoint (e.g. after a reconnect) is
  suppressed.
- `error` frames clear `pendingAction` so a rejected approve/redirect doesn't permanently
  disable the checkpoint controls, and render as a visible list entry rather than being
  swallowed.

### Dependencies added
None.
