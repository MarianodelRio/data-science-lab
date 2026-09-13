import { useEffect, useMemo, useRef, useState } from 'react'
import { getRun, subscribeToRunEvents } from '../api/client'
import type { PipelineEvent } from '../api/types'

/**
 * Connection/display state for the pipeline view. `'live'` and
 * `'reconnecting'` both mean "subscribed", they differ only in whether the
 * stream is currently flowing.
 */
type ConnectionState =
  | 'no_run'
  | 'connecting'
  | 'live'
  | 'reconnecting'
  | 'awaiting_input'
  | 'completed'
  | 'failed'

interface TimelineEntry {
  id: number
  node: string
  status: 'running' | 'done'
  durationMs: number | null
  outputSummary: string | null
}

const STATUS_BANNER: Partial<Record<ConnectionState, string>> = {
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting…',
  awaiting_input: 'Awaiting human input — see the Chat tab.',
  completed: 'Run completed.',
  failed: 'Run failed.',
}

/** FIFO correlation key for a node's start/end pair (see design decision 1). */
function correlationKey(event: PipelineEvent): string {
  return `${event.phase}::${event.iteration}::${event.node}`
}

/**
 * Applies one SSE event to the timeline, per the FIFO-queue correlation
 * rule: the same node can run more than once sequentially within the same
 * {phase, iteration} (critic-retry invariant), so entries are matched
 * oldest-start-first rather than by a plain node-name map.
 */
function applyEventToTimeline(
  timeline: TimelineEntry[],
  openEntries: Map<string, number[]>,
  event: PipelineEvent,
  nextId: number,
): TimelineEntry[] {
  const key = correlationKey(event)

  if (event.event === 'start') {
    const entry: TimelineEntry = {
      id: nextId,
      node: event.node,
      status: 'running',
      durationMs: null,
      outputSummary: null,
    }
    const queue = openEntries.get(key) ?? []
    queue.push(entry.id)
    openEntries.set(key, queue)
    return [...timeline, entry]
  }

  const queue = openEntries.get(key)
  const openId = queue?.shift()
  if (openId === undefined) {
    // An `end` with no open `start` for its key (subscribed mid-run) — append
    // a new entry directly in the `done` state rather than dropping it.
    return [
      ...timeline,
      {
        id: nextId,
        node: event.node,
        status: 'done',
        durationMs: event.duration_ms,
        outputSummary: event.output_summary,
      },
    ]
  }
  return timeline.map((entry) =>
    entry.id === openId
      ? {
          ...entry,
          status: 'done',
          durationMs: event.duration_ms,
          outputSummary: event.output_summary,
        }
      : entry,
  )
}

export function PipelineView({ runId }: { runId?: string }) {
  const [connectionState, setConnectionState] = useState<ConnectionState>(
    runId ? 'connecting' : 'no_run',
  )
  const [timeline, setTimeline] = useState<TimelineEntry[]>([])
  const [currentPhase, setCurrentPhase] = useState<string | null>(null)

  // Reset all run-scoped state during render when `runId` changes, per
  // React's documented "adjusting state when a prop changes" pattern —
  // avoids an unconditional setState at the top of the sync effect below,
  // which the linter (correctly) flags as deriving state that render can
  // compute directly instead.
  const [prevRunId, setPrevRunId] = useState(runId)
  if (runId !== prevRunId) {
    setPrevRunId(runId)
    setConnectionState(runId ? 'connecting' : 'no_run')
    setTimeline([])
    setCurrentPhase(null)
  }

  const openEntriesRef = useRef<Map<string, number[]>>(new Map())
  const nextEntryIdRef = useRef(0)
  const unsubscribeRef = useRef<(() => void) | null>(null)
  // 0 = no status check in flight; otherwise the id of the currently valid
  // getRun() check, so a stale resolution (superseded by recovery or a newer
  // check) can be ignored (design decision 4).
  const pendingCheckIdRef = useRef(0)
  const nextCheckIdRef = useRef(1)

  // "Active node(s)" must never contradict a terminal banner: once the
  // stream is no longer actually flowing (or the run is paused awaiting
  // input), nothing is really "active" any more, regardless of what
  // `timeline` entries are still stuck in `running` — e.g. because their
  // matching `end` event was in flight or dropped by the backend's bounded,
  // lossy SSE queue (see docs/api.md § SSE). [ADV-86bef2c6]
  const isStreamLive =
    connectionState === 'live' ||
    connectionState === 'connecting' ||
    connectionState === 'reconnecting'

  const activeNodes = useMemo(
    () =>
      isStreamLive
        ? timeline
            .filter((entry) => entry.status === 'running')
            .map((e) => e.node)
        : [],
    [timeline, isStreamLive],
  )

  useEffect(() => {
    if (!runId) return

    let cancelled = false
    openEntriesRef.current = new Map()
    pendingCheckIdRef.current = 0

    const checkRunStatus = () => {
      const checkId = nextCheckIdRef.current++
      pendingCheckIdRef.current = checkId
      getRun(runId)
        .then((run) => {
          if (cancelled || pendingCheckIdRef.current !== checkId) return
          if (run.status === 'interrupted') {
            setConnectionState('awaiting_input')
          } else if (run.status === 'completed' || run.status === 'failed') {
            setConnectionState(run.status)
            unsubscribeRef.current?.()
          } else {
            setConnectionState('reconnecting')
          }
        })
        .catch((error: unknown) => {
          if (cancelled || pendingCheckIdRef.current !== checkId) return
          console.error(
            `PipelineView: getRun(${runId}) failed while diagnosing a connection drop`,
            error,
          )
          setConnectionState('reconnecting')
        })
    }

    const onEvent = (event: PipelineEvent) => {
      if (cancelled) return
      // A message arrived, so the stream is alive — any in-flight status
      // check is now stale.
      pendingCheckIdRef.current = 0
      setConnectionState('live')
      setCurrentPhase(event.phase)
      const id = nextEntryIdRef.current++
      setTimeline((prev) =>
        applyEventToTimeline(prev, openEntriesRef.current, event, id),
      )
    }

    const onError = (error: Event | Error) => {
      if (cancelled) return
      // Only a genuine connection Event should trigger a status check — a
      // JS Error means one message failed to parse, the stream is otherwise
      // still alive (see client.ts's onmessage try/catch).
      if (!(error instanceof Event)) return
      checkRunStatus()
    }

    unsubscribeRef.current = subscribeToRunEvents(runId, { onEvent, onError })

    return () => {
      cancelled = true
      unsubscribeRef.current?.()
      unsubscribeRef.current = null
    }
  }, [runId])

  if (connectionState === 'no_run') {
    return <p>No run selected.</p>
  }

  const banner = STATUS_BANNER[connectionState]

  return (
    <div>
      <h2>Pipeline</h2>
      <p role="status">{banner ?? ''}</p>
      <p>Phase: {currentPhase ?? '—'}</p>
      <p>
        Active node(s): {activeNodes.length > 0 ? activeNodes.join(', ') : '—'}
      </p>
      <ol aria-label="Node timeline">
        {timeline.map((entry) => (
          <li key={entry.id}>
            {entry.status === 'running'
              ? isStreamLive
                ? `${entry.node} — running`
                : `${entry.node} — running (unresolved)`
              : `${entry.node} — ${entry.durationMs}ms`}
          </li>
        ))}
      </ol>
    </div>
  )
}
