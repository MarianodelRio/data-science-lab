import { useEffect, useRef, useState, type ReactNode } from 'react'
import { connectChat } from '../api/client'
import type { ChatClientFrame, ChatServerFrame } from '../api/types'

type ConnectionState = 'no_run' | 'connecting' | 'open' | 'reconnecting' | 'disconnected'

type ChatEntry =
  | { kind: 'question'; id: number; text: string }
  | { kind: 'answer'; id: number; text: string }
  | { kind: 'checkpoint'; id: number; phase: string; summary: string }
  | { kind: 'error'; id: number; detail: string }

export const MAX_RECONNECT_ATTEMPTS = 5
export const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 10000]

const STATUS_BANNER: Partial<Record<ConnectionState, string>> = {
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting…',
  disconnected: 'Unable to reconnect. Reload to try again.',
}

export function Chat({ runId }: { runId?: string }) {
  const [connectionState, setConnectionState] = useState<ConnectionState>(
    runId ? 'connecting' : 'no_run',
  )
  const [entries, setEntries] = useState<ChatEntry[]>([])
  const [activeCheckpoint, setActiveCheckpoint] =
    useState<{ phase: string; summary: string } | null>(null)
  const [pendingAction, setPendingAction] = useState<'approve' | 'redirect' | null>(null)
  const [inputValue, setInputValue] = useState('')
  const [feedbackValue, setFeedbackValue] = useState('')

  const connectionRef = useRef<ReturnType<typeof connectChat> | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectAttemptsRef = useRef(0)
  const lastCheckpointRef = useRef<{ phase: string; summary: string } | null>(null)
  const nextEntryIdRef = useRef(0)
  const feedbackRef = useRef<HTMLTextAreaElement | null>(null)

  // Render-time reset on runId change only (avoids a setState-in-effect
  // eslint-plugin-react-hooks violation) — switching to a different run
  // clears history and connection state; a same-runId reconnect must NOT
  // hit this path.
  const [prevRunId, setPrevRunId] = useState(runId)
  if (runId !== prevRunId) {
    setPrevRunId(runId)
    setConnectionState(runId ? 'connecting' : 'no_run')
    setEntries([])
    setActiveCheckpoint(null)
    setPendingAction(null)
    setInputValue('')
    setFeedbackValue('')
  }

  useEffect(() => {
    // This effect re-runs exactly once per runId change (its dependency
    // array is [runId]; a same-runId reconnect happens inside connect()/
    // scheduleReconnect() below without re-running the effect), so
    // resetting these two refs here — rather than in the render-time
    // block above, which eslint-plugin-react-hooks forbids mutating refs
    // in — still fires exactly once per new run and never on a same-run
    // reconnect.
    reconnectAttemptsRef.current = 0
    lastCheckpointRef.current = null

    if (!runId) return

    let cancelled = false
    let intentionalClose = false

    const appendEntry = (entry: ChatEntry) => setEntries((prev) => [...prev, entry])

    const handleFrame = (frame: ChatServerFrame) => {
      switch (frame.type) {
        case 'checkpoint': {
          const current = { phase: frame.phase, summary: frame.summary }
          setActiveCheckpoint(current)
          // A checkpoint frame means the run is (still) interrupted, so any
          // pending approve/redirect from before a drop was never applied —
          // clear it on every arrival (including a re-announced/deduped
          // one after reconnect), not just the first time, so the controls
          // don't stay permanently disabled.
          setPendingAction(null)
          const last = lastCheckpointRef.current
          if (last && last.phase === current.phase && last.summary === current.summary) {
            return // dedupe: same checkpoint re-announced after a reconnect
          }
          lastCheckpointRef.current = current
          appendEntry({ kind: 'checkpoint', id: nextEntryIdRef.current++, ...current })
          return
        }
        case 'answer':
          appendEntry({ kind: 'answer', id: nextEntryIdRef.current++, text: frame.text })
          return
        case 'resumed':
          setActiveCheckpoint(null)
          lastCheckpointRef.current = null
          setPendingAction(null)
          setFeedbackValue('')
          return
        case 'error':
          setPendingAction(null)
          appendEntry({ kind: 'error', id: nextEntryIdRef.current++, detail: frame.detail })
          return
      }
    }

    const scheduleReconnect = () => {
      if (reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
        setConnectionState('disconnected')
        return
      }
      setConnectionState('reconnecting')
      const delay =
        RECONNECT_DELAYS_MS[
          Math.min(reconnectAttemptsRef.current, RECONNECT_DELAYS_MS.length - 1)
        ]
      reconnectAttemptsRef.current += 1
      reconnectTimeoutRef.current = setTimeout(() => {
        if (!cancelled) connect()
      }, delay)
    }

    const connect = () => {
      const connection = connectChat(runId)
      connectionRef.current = connection

      connection.onOpen(() => {
        if (cancelled) return
        reconnectAttemptsRef.current = 0
        setConnectionState('open')
      })
      connection.onMessage((frame) => {
        if (!cancelled) handleFrame(frame)
      })
      connection.onError((error) => {
        if (!cancelled) {
          console.error(`Chat: connection error for run ${runId}`, error)
        }
      })
      connection.onClose(() => {
        // Native WebSocket.close() fires onclose asynchronously, so a
        // connection closed during cleanup can report its close after a
        // newer connection (for a new runId) is already the active one.
        // Only clear the ref if it still points at *this* connection —
        // otherwise this stale close would null out the live connection.
        if (connectionRef.current === connection) connectionRef.current = null
        if (cancelled || intentionalClose) return
        scheduleReconnect()
      })
    }

    connect()

    return () => {
      cancelled = true
      intentionalClose = true
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current)
      connectionRef.current?.close()
      connectionRef.current = null
    }
  }, [runId])

  useEffect(() => {
    if (activeCheckpoint) feedbackRef.current?.focus()
  }, [activeCheckpoint])

  const sendFrame = (frame: ChatClientFrame) => connectionRef.current?.send(frame)

  const handleSend = () => {
    const text = inputValue.trim()
    if (!text) return
    setEntries((prev) => [...prev, { kind: 'question', id: nextEntryIdRef.current++, text }])
    sendFrame({ type: 'question', text })
    setInputValue('')
  }

  const handleApprove = () => {
    if (!activeCheckpoint) return
    setPendingAction('approve')
    sendFrame({ type: 'approve', feedback: feedbackValue })
  }

  const handleRedirect = () => {
    if (!activeCheckpoint) return
    setPendingAction('redirect')
    sendFrame({ type: 'redirect', feedback: feedbackValue })
  }

  if (connectionState === 'no_run') {
    return (
      <div>
        <h2>Chat</h2>
        <p>No run selected.</p>
      </div>
    )
  }

  const banner = STATUS_BANNER[connectionState]
  const controlsDisabled = pendingAction !== null || connectionState !== 'open'

  return (
    <div>
      <h2>Chat</h2>
      <p role="status">{banner ?? ''}</p>
      <ul aria-label="Messages">
        {entries.map((entry) => (
          <li key={entry.id}>{renderEntry(entry)}</li>
        ))}
      </ul>
      {activeCheckpoint && (
        <div>
          <p>Phase: {activeCheckpoint.phase || '—'}</p>
          <p>{activeCheckpoint.summary || 'No summary provided.'}</p>
          <label htmlFor="chat-feedback">Feedback</label>
          <textarea
            id="chat-feedback"
            ref={feedbackRef}
            value={feedbackValue}
            onChange={(e) => setFeedbackValue(e.target.value)}
          />
          <button type="button" onClick={handleApprove} disabled={controlsDisabled}>
            Approve
          </button>
          <button type="button" onClick={handleRedirect} disabled={controlsDisabled}>
            Redirect
          </button>
        </div>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          handleSend()
        }}
      >
        <label htmlFor="chat-message">Message</label>
        <input
          id="chat-message"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
        />
        <button type="submit" disabled={inputValue.trim().length === 0 || connectionState !== 'open'}>
          Send
        </button>
      </form>
    </div>
  )
}

function renderEntry(entry: ChatEntry): ReactNode {
  switch (entry.kind) {
    case 'question':
      return `You: ${entry.text}`
    case 'answer':
      return `Explainer: ${entry.text}`
    case 'checkpoint':
      return `Checkpoint (${entry.phase || '—'}): ${entry.summary || 'No summary provided.'}`
    case 'error':
      return `Error: ${entry.detail}`
  }
}
