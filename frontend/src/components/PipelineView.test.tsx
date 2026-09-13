import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PipelineView } from './PipelineView'
import * as client from '../api/client'
import type { RunEventHandlers } from '../api/client'
import type { PipelineEvent, Run } from '../api/types'

vi.mock('../api/client', () => ({
  subscribeToRunEvents: vi.fn(),
  getRun: vi.fn(),
  resumeRun: vi.fn(),
}))

const subscribeToRunEvents = vi.mocked(client.subscribeToRunEvents)
const getRun = vi.mocked(client.getRun)
const resumeRun = vi.mocked(client.resumeRun)

/** Captures the handlers passed to subscribeToRunEvents and returns a spy unsubscribe fn. */
function stubSubscription() {
  let handlers: RunEventHandlers | undefined
  const unsubscribe = vi.fn()
  subscribeToRunEvents.mockImplementation((_runId, h) => {
    handlers = h
    return unsubscribe
  })
  return {
    unsubscribe,
    emit: (event: PipelineEvent) => act(() => handlers?.onEvent(event)),
    error: (error: Event | Error) => {
      act(() => handlers?.onError?.(error))
    },
  }
}

function makeEvent(overrides: Partial<PipelineEvent>): PipelineEvent {
  return {
    timestamp: '2026-09-13T00:00:00Z',
    run_id: 'run-1',
    iteration: 1,
    phase: 'phase2_research',
    node: 'researcher',
    event: 'start',
    duration_ms: null,
    output_summary: null,
    ...overrides,
  }
}

function makeRun(overrides: Partial<Run>): Run {
  return {
    run_id: 'run-1',
    competition_name: 'titanic',
    workspace_path: '/workspaces/titanic',
    status: 'running',
    phase: 'phase2_research',
    current_iteration: 1,
    best_score: null,
    created_at: '2026-09-13T00:00:00Z',
    updated_at: '2026-09-13T00:00:00Z',
    ...overrides,
  }
}

beforeEach(() => {
  subscribeToRunEvents.mockReset()
  getRun.mockReset()
  resumeRun.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('PipelineView — no run', () => {
  it('renders "No run selected." and does not subscribe', () => {
    render(<PipelineView />)

    expect(screen.getByText('No run selected.')).toBeInTheDocument()
    expect(subscribeToRunEvents).not.toHaveBeenCalled()
  })
})

describe('PipelineView — connecting', () => {
  it('shows the connecting banner before any event arrives', () => {
    stubSubscription()

    render(<PipelineView runId="run-1" />)

    expect(screen.getByRole('status')).toHaveTextContent(/connecting/i)
  })
})

describe('PipelineView — timeline correlation', () => {
  it('appends nodes in arrival order and tracks each one running -> done', () => {
    const sub = stubSubscription()
    render(<PipelineView runId="run-1" />)

    sub.emit(makeEvent({ node: 'nodeA', event: 'start', phase: 'phase2' }))
    sub.emit(makeEvent({ node: 'nodeB', event: 'start', phase: 'phase2' }))
    sub.emit(
      makeEvent({
        node: 'nodeA',
        event: 'end',
        phase: 'phase2',
        duration_ms: 100,
        output_summary: 'done A',
      }),
    )
    sub.emit(
      makeEvent({
        node: 'nodeB',
        event: 'end',
        phase: 'phase2',
        duration_ms: 200,
        output_summary: 'done B',
      }),
    )

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('nodeA — 100ms')
    expect(items[1]).toHaveTextContent('nodeB — 200ms')
    expect(screen.getByText(/phase: phase2/i)).toBeInTheDocument()
  })

  it('correlates a retried node via FIFO order, not by name', () => {
    const sub = stubSubscription()
    render(<PipelineView runId="run-1" />)

    sub.emit(
      makeEvent({
        node: 'nodeA',
        event: 'start',
        phase: 'phase2',
        iteration: 1,
      }),
    )
    sub.emit(
      makeEvent({
        node: 'nodeA',
        event: 'start',
        phase: 'phase2',
        iteration: 1,
      }),
    )
    sub.emit(
      makeEvent({
        node: 'nodeA',
        event: 'end',
        phase: 'phase2',
        iteration: 1,
        duration_ms: 10,
      }),
    )
    sub.emit(
      makeEvent({
        node: 'nodeA',
        event: 'end',
        phase: 'phase2',
        iteration: 1,
        duration_ms: 20,
      }),
    )

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('nodeA — 10ms')
    expect(items[1]).toHaveTextContent('nodeA — 20ms')
  })

  it('appends a done entry for an end with no matching prior start', () => {
    const sub = stubSubscription()
    render(<PipelineView runId="run-1" />)

    sub.emit(
      makeEvent({
        node: 'lateJoinNode',
        event: 'end',
        duration_ms: 42,
        output_summary: 'joined mid-run',
      }),
    )

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(1)
    expect(items[0]).toHaveTextContent('lateJoinNode — 42ms')
  })
})

describe('PipelineView — terminal/interrupt detection', () => {
  it('switches to awaiting-input on an interrupted run, without calling resumeRun', async () => {
    const sub = stubSubscription()
    getRun.mockResolvedValue(makeRun({ status: 'interrupted' }))
    render(<PipelineView runId="run-1" />)

    sub.error(new Event('error'))

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        /awaiting human input/i,
      ),
    )
    expect(resumeRun).not.toHaveBeenCalled()
  })

  it.each(['completed', 'failed'] as const)(
    'shows a final state and unsubscribes exactly once when getRun resolves %s',
    async (status) => {
      const sub = stubSubscription()
      getRun.mockResolvedValue(makeRun({ status }))
      render(<PipelineView runId="run-1" />)

      sub.error(new Event('error'))

      await waitFor(() =>
        expect(screen.getByRole('status')).toHaveTextContent(
          status === 'completed' ? /run completed/i : /run failed/i,
        ),
      )
      expect(sub.unsubscribe).toHaveBeenCalledTimes(1)
    },
  )

  it.each(['pending', 'running'] as const)(
    'shows reconnecting and does not unsubscribe when getRun resolves %s',
    async (status) => {
      const sub = stubSubscription()
      getRun.mockResolvedValue(makeRun({ status }))
      render(<PipelineView runId="run-1" />)

      sub.error(new Event('error'))

      await waitFor(() =>
        expect(screen.getByRole('status')).toHaveTextContent(/reconnecting/i),
      )
      expect(sub.unsubscribe).not.toHaveBeenCalled()
    },
  )

  it('ignores a malformed-message Error — no state change, no getRun call', () => {
    const sub = stubSubscription()
    render(<PipelineView runId="run-1" />)

    sub.error(new Error('bad json'))

    expect(getRun).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent(/connecting/i)
  })

  it('falls back to reconnecting when getRun rejects, without crashing', async () => {
    const sub = stubSubscription()
    getRun.mockRejectedValue(new Error('network down'))
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<PipelineView runId="run-1" />)

    sub.error(new Event('error'))

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/reconnecting/i),
    )
  })
})

describe('PipelineView — active nodes reconciliation', () => {
  it.each(['completed', 'failed'] as const)(
    'clears "Active node(s)" once the terminal banner for %s is shown, even with a stranded running entry',
    async (status) => {
      const sub = stubSubscription()
      getRun.mockResolvedValue(makeRun({ status }))
      render(<PipelineView runId="run-1" />)

      // `trainer` starts but its `end` event never arrives (dropped by the
      // backend's bounded SSE queue, or simply in flight when the stream
      // drops) — its timeline entry is stranded in `running`.
      sub.emit(makeEvent({ node: 'trainer', event: 'start', phase: 'phase4' }))
      expect(screen.getByText(/active node\(s\):\s*trainer/i)).toBeInTheDocument()

      sub.error(new Event('error'))

      await waitFor(() =>
        expect(screen.getByRole('status')).toHaveTextContent(
          status === 'completed' ? /run completed/i : /run failed/i,
        ),
      )
      // The terminal banner and a non-empty "Active node(s)" line must never
      // both be shown at once. [ADV-86bef2c6]
      expect(screen.getByText(/active node\(s\):\s*—/i)).toBeInTheDocument()
      expect(screen.queryByText(/active node\(s\):\s*trainer/i)).toBeNull()
      expect(screen.getByText(/trainer — running \(unresolved\)/i)).toBeInTheDocument()
    },
  )

  it('clears "Active node(s)" once the run is awaiting input', async () => {
    const sub = stubSubscription()
    getRun.mockResolvedValue(makeRun({ status: 'interrupted' }))
    render(<PipelineView runId="run-1" />)

    sub.emit(makeEvent({ node: 'trainer', event: 'start', phase: 'phase4' }))
    sub.error(new Event('error'))

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        /awaiting human input/i,
      ),
    )
    expect(screen.getByText(/active node\(s\):\s*—/i)).toBeInTheDocument()
  })

  it('keeps "Active node(s)" populated while merely reconnecting', async () => {
    const sub = stubSubscription()
    getRun.mockResolvedValue(makeRun({ status: 'running' }))
    render(<PipelineView runId="run-1" />)

    sub.emit(makeEvent({ node: 'trainer', event: 'start', phase: 'phase4' }))
    sub.error(new Event('error'))

    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/reconnecting/i),
    )
    expect(screen.getByText(/active node\(s\):\s*trainer/i)).toBeInTheDocument()
    expect(screen.getByText(/trainer — running$/i)).toBeInTheDocument()
  })
})

describe('PipelineView — lifecycle', () => {
  it('unsubscribes exactly once on unmount', () => {
    const sub = stubSubscription()
    const { unmount } = render(<PipelineView runId="run-1" />)

    unmount()

    expect(sub.unsubscribe).toHaveBeenCalledTimes(1)
  })

  it('resets the timeline and resubscribes when runId changes', () => {
    const firstUnsubscribe = vi.fn()
    const secondUnsubscribe = vi.fn()
    let callCount = 0
    subscribeToRunEvents.mockImplementation(() => {
      callCount += 1
      return callCount === 1 ? firstUnsubscribe : secondUnsubscribe
    })

    const { rerender } = render(<PipelineView runId="run-1" />)
    rerender(<PipelineView runId="run-2" />)

    expect(firstUnsubscribe).toHaveBeenCalledTimes(1)
    expect(subscribeToRunEvents).toHaveBeenNthCalledWith(
      2,
      'run-2',
      expect.anything(),
    )
    expect(screen.queryAllByRole('listitem')).toHaveLength(0)
  })
})
