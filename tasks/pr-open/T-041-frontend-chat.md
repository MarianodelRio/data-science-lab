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

## Completed — review-fix round (adversarial findings)

Fixed one HIGH-severity, verified, production-reachable bug plus one related MEDIUM bug and
two adjacent WARNING-level code-quality issues, all in `frontend/src/components/Chat.tsx`, per
the adversarial reviewer's findings. New commit on `feature/T-041-frontend-chat`, does not amend
the original implementation commit (`ee0eaef`).

- **Finding 1 (HIGH) — stale `onClose` nulls the live connection after a `runId` switch.**
  `connect()`'s `onClose` handler unconditionally set `connectionRef.current = null`. Native
  `WebSocket.close()` fires `onclose` asynchronously, so the old connection's deferred close
  (triggered by the effect cleanup on a `runId` change) could arrive *after* the new connection
  for the new `runId` was already live and referenced by `connectionRef`, nulling out the
  current, working connection. Result: `connectionState === 'open'` (UI fully enabled) but
  `connectionRef.current === null`, so `sendFrame` silently dropped every message for the rest of
  that run's session. Fixed by capturing the connection identity in a local `connection` const at
  `connect()` time and only clearing the ref if it still points at that same connection:
  `if (connectionRef.current === connection) connectionRef.current = null`. Regression test added
  to `Chat — runId changes`: opens connection A, switches `runId`, opens connection B, then fires
  A's close listener (simulating the deferred native close arriving late), then asserts a send
  goes to B (`connections[1].sent`), not silently dropped. Verified the test fails against the
  pre-fix code (drops the message, asserts `[]`) and passes against the fix.

- **Finding 2 (MEDIUM) — `pendingAction` never cleared by a re-announced checkpoint.** The
  backend re-sends the same `checkpoint` frame on every reconnect while a run is still
  interrupted. If a user's Approve/Redirect was sent but the connection dropped before a
  `resumed`/`error` reply arrived, `pendingAction` stayed set forever — the re-announced
  checkpoint after reconnect never cleared it, permanently disabling Approve/Redirect with no
  recovery short of a page reload. Fixed by resetting `pendingAction` to `null` at the top of the
  `'checkpoint'` case in `handleFrame`, before the dedupe check, so it fires on every checkpoint
  arrival (including a deduped repeat), not just the first. Regression test added to
  `Chat — checkpoint controls`: checkpoint arrives, Approve is clicked (button disabled), the
  same checkpoint is re-emitted, then asserts Approve/Redirect are enabled again. Verified the
  test fails against the pre-fix code and passes against the fix.

- **CQ-6def14dd (WARNING, bundled) — `reconnectAttemptsRef` not reset on `runId` change.** A new
  run inherited a leftover reconnect-attempt count from the previous run, reducing how many
  reconnect attempts the new run gets before "Unable to reconnect" if its first connection
  attempt fails before opening.
- **CQ-4c032ef9 (WARNING, bundled) — `lastCheckpointRef` not reset on `runId` change.** Could
  wrongly dedupe a new run's first checkpoint if it happened to share the exact same
  `{phase, summary}` as the last one seen for the previous run.

  Both fixed by resetting `reconnectAttemptsRef.current = 0` and `lastCheckpointRef.current =
  null`. **Deviation from the task description's suggested placement:** the description offered
  resetting these directly in the render-time `prevRunId`-change block (alongside the existing
  `setEntries([])` etc. calls), noting it was "almost certainly fine" but deferring to judgment
  after reading the code. That placement was tried first and fails lint: `eslint-plugin-react-
  hooks`'s `react-hooks/refs` rule (already enabled in this project's config) flags direct
  `ref.current` mutation during render as a hard error ("Cannot access refs during render"), not
  just a style nit — the "technically allowed by React" caveat in the task description doesn't
  hold against this project's actual lint config. Moved the two resets instead to the top of the
  main `useEffect` body (before the `if (!runId) return` guard). That effect's dependency array
  is `[runId]`, so it re-runs exactly once per `runId` change and never on a same-`runId`
  reconnect (which happens via `connect()`/`scheduleReconnect()` calls *within* one effect
  execution, not a re-run) — same reset semantics as the render-time block would have had,
  without the lint violation.

- **Not fixed (explicitly out of scope per the review-fix instructions):** ADV Finding 3
  (error-frame reconnect churn on unknown `run_id` — needs a backend contract change),
  CQ-1e823b48 (feedback textarea refocus nitpick), CQ-a7c20eea (stale docstring nitpick),
  SEC-104cb40d/SEC-e869118b (sanctioned framework mechanism / INFO-level, not real issues).

### Verification
`npm run lint` (0 errors, 1 pre-existing unrelated warning), `npm test` (72/72 passing across 5
files, including 19 in `Chat.test.tsx` — 17 original + 2 new regression tests), `npm run build`
(clean). Run via `docker run ... node:22-bullseye` per the established pattern (host Node v16.17.0
is below the `>=20.19.0` engines requirement). Both new regression tests confirmed to fail against
the pre-fix code and pass against the fix.
