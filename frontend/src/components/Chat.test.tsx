import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Chat, MAX_RECONNECT_ATTEMPTS, RECONNECT_DELAYS_MS } from './Chat'
import * as client from '../api/client'
import type { ChatClientFrame, ChatServerFrame } from '../api/types'

vi.mock('../api/client', () => ({
  connectChat: vi.fn(),
}))

const connectChat = vi.mocked(client.connectChat)

interface StubConnection {
  sent: ChatClientFrame[]
  emit: (frame: ChatServerFrame) => void
  open: () => void
  error: (error: Event | Error) => void
  close: (event?: Partial<CloseEvent>) => void
  closeSpy: ReturnType<typeof vi.fn>
}

/**
 * Stubs one connectChat() call, capturing the listeners the component
 * registers and pushing the resulting stub into `connections` so
 * reconnect tests can address connections[0], connections[1], etc.
 */
function stubNextConnection(connections: StubConnection[]) {
  connectChat.mockImplementationOnce(() => {
    let messageListener: ((frame: ChatServerFrame) => void) | undefined
    let openListener: (() => void) | undefined
    let errorListener: ((error: Event | Error) => void) | undefined
    let closeListener: ((event: CloseEvent) => void) | undefined
    const sent: ChatClientFrame[] = []
    const closeSpy = vi.fn()

    const stub: StubConnection = {
      sent,
      emit: (frame) => act(() => messageListener?.(frame)),
      open: () => act(() => openListener?.()),
      error: (error) => act(() => errorListener?.(error)),
      close: (event = {}) => act(() => closeListener?.(event as CloseEvent)),
      closeSpy,
    }
    connections.push(stub)

    return {
      send: (frame: ChatClientFrame) => sent.push(frame),
      onMessage: (listener: (frame: ChatServerFrame) => void) => {
        messageListener = listener
      },
      onOpen: (listener: () => void) => {
        openListener = listener
      },
      onError: (listener: (error: Event | Error) => void) => {
        errorListener = listener
      },
      onClose: (listener: (event: CloseEvent) => void) => {
        closeListener = listener
      },
      close: closeSpy,
    }
  })
}

function checkpointFrame(overrides: Partial<{ phase: string; summary: string }> = {}) {
  return {
    type: 'checkpoint' as const,
    phase: 'phase3_baseline',
    summary: 'Baseline trained.',
    ...overrides,
  }
}

beforeEach(() => {
  connectChat.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('Chat — no run', () => {
  it('renders "No run selected." and never calls connectChat', () => {
    render(<Chat />)

    expect(screen.getByText('No run selected.')).toBeInTheDocument()
    expect(connectChat).not.toHaveBeenCalled()
  })
})

describe('Chat — connecting', () => {
  it('opens a connection when runId is provided', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)

    render(<Chat runId="run-1" />)

    expect(connectChat).toHaveBeenCalledWith('run-1')
  })
})

describe('Chat — messaging', () => {
  it('sends a question frame and renders the reply once the answer frame arrives', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()

    fireEvent.change(screen.getByLabelText('Message'), {
      target: { value: 'what is the CV score?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    expect(connections[0].sent).toEqual([
      { type: 'question', text: 'what is the CV score?' },
    ])
    expect(screen.getByText('You: what is the CV score?')).toBeInTheDocument()

    connections[0].emit({ type: 'answer', text: '0.83' })

    expect(screen.getByText('Explainer: 0.83')).toBeInTheDocument()
  })

  it('disables Send when input is blank/whitespace-only and does not call send', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()

    const sendButton = screen.getByRole('button', { name: 'Send' })
    expect(sendButton).toBeDisabled()

    fireEvent.change(screen.getByLabelText('Message'), { target: { value: '   ' } })
    expect(sendButton).toBeDisabled()

    fireEvent.click(sendButton)
    expect(connections[0].sent).toEqual([])
  })
})

describe('Chat — checkpoint controls', () => {
  it('surfaces checkpoint summary and Approve/Redirect controls when a checkpoint frame arrives', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()

    connections[0].emit(checkpointFrame())

    expect(screen.getByText('Phase: phase3_baseline')).toBeInTheDocument()
    expect(screen.getByText('Baseline trained.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Redirect' })).toBeInTheDocument()
  })

  it('has no Approve/Redirect controls before any checkpoint frame arrives, even once open', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()

    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Redirect' })).toBeNull()
  })

  it('shows Approve/Redirect controls for a checkpoint whose phase/summary are both ""', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()

    connections[0].emit(checkpointFrame({ phase: '', summary: '' }))

    expect(screen.getByRole('button', { name: 'Approve' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Redirect' })).toBeInTheDocument()
  })

  it('sends {type: approve, feedback: ""} when Approve is clicked with empty feedback', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()
    connections[0].emit(checkpointFrame())

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    expect(connections[0].sent).toEqual([{ type: 'approve', feedback: '' }])
  })

  it('sends {type: redirect, feedback: "<entered text>"} after typing feedback', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()
    connections[0].emit(checkpointFrame())

    fireEvent.change(screen.getByLabelText('Feedback'), {
      target: { value: 'try a different model' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Redirect' }))

    expect(connections[0].sent).toEqual([
      { type: 'redirect', feedback: 'try a different model' },
    ])
  })

  it('clears checkpoint controls and pendingAction after a resumed frame', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()
    connections[0].emit(checkpointFrame())
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    connections[0].emit({ type: 'resumed', run_id: 'run-1', status: 'running' })

    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Redirect' })).toBeNull()
  })

  it('appends an "Error: ..." entry and clears pendingAction on an error frame', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()
    connections[0].emit(checkpointFrame())
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()

    connections[0].emit({ type: 'error', detail: 'feedback rejected' })

    expect(screen.getByText('Error: feedback rejected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve' })).not.toBeDisabled()
  })

  it('appends only one entry for the same {phase, summary} checkpoint received twice', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()

    connections[0].emit(checkpointFrame())
    connections[0].emit(checkpointFrame())

    expect(
      screen.getAllByText(/Checkpoint \(phase3_baseline\): Baseline trained\./),
    ).toHaveLength(1)
  })

  it('focuses the feedback textarea when a checkpoint frame first arrives', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()

    connections[0].emit(checkpointFrame())

    expect(screen.getByLabelText('Feedback')).toHaveFocus()
  })
})

describe('Chat — reconnect', () => {
  it('shows "Reconnecting…" immediately after an unintentional close, before any timer advances', () => {
    vi.useFakeTimers()
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    render(<Chat runId="run-1" />)
    connections[0].open()

    connections[0].close()

    expect(screen.getByRole('status')).toHaveTextContent(/reconnecting/i)
  })

  it('reconnects through the full backoff schedule, preserving history, then stops after 5 attempts', () => {
    vi.useFakeTimers()
    const connections: StubConnection[] = []
    for (let i = 0; i <= MAX_RECONNECT_ATTEMPTS; i += 1) {
      stubNextConnection(connections)
    }
    render(<Chat runId="run-1" />)
    connections[0].open()
    connections[0].emit({ type: 'answer', text: 'kept across reconnects' })
    connections[0].close()

    RECONNECT_DELAYS_MS.forEach((delay, i) => {
      act(() => {
        vi.advanceTimersByTime(delay)
      })
      // Each advance creates connections[i + 1] (the next connectChat call).
      expect(
        screen.getByText('Explainer: kept across reconnects'),
      ).toBeInTheDocument()
      // Keep the chain going by dropping the connection just opened, except
      // after the final delay — that would push a 7th call past the cap.
      if (i < RECONNECT_DELAYS_MS.length - 1) {
        connections[i + 1].close()
      }
    })

    expect(connectChat).toHaveBeenCalledTimes(1 + RECONNECT_DELAYS_MS.length)
    expect(connectChat).toHaveBeenCalledTimes(6)
  })

  it('stops retrying after all 5 reconnect attempts drop, and shows "Unable to reconnect."', () => {
    vi.useFakeTimers()
    const connections: StubConnection[] = []
    for (let i = 0; i <= MAX_RECONNECT_ATTEMPTS; i += 1) {
      stubNextConnection(connections)
    }
    render(<Chat runId="run-1" />)
    connections[0].open()
    connections[0].close()

    for (const delay of RECONNECT_DELAYS_MS) {
      act(() => {
        vi.advanceTimersByTime(delay)
      })
      const last = connections[connections.length - 1]
      last.close()
    }

    expect(connectChat).toHaveBeenCalledTimes(6)
    act(() => {
      vi.advanceTimersByTime(60_000)
    })
    expect(connectChat).toHaveBeenCalledTimes(6)
    expect(screen.getByRole('status')).toHaveTextContent(/unable to reconnect/i)
  })
})

describe('Chat — runId changes', () => {
  it('clears history, opens a new connection, and closes the old one when runId changes', () => {
    const connections: StubConnection[] = []
    stubNextConnection(connections)
    stubNextConnection(connections)
    const { rerender } = render(<Chat runId="run-1" />)
    connections[0].open()
    connections[0].emit({ type: 'answer', text: 'first run answer' })
    expect(screen.getByText('Explainer: first run answer')).toBeInTheDocument()

    rerender(<Chat runId="run-2" />)

    expect(connections[0].closeSpy).toHaveBeenCalledTimes(1)
    expect(connectChat).toHaveBeenNthCalledWith(2, 'run-2')
    expect(screen.queryByText('Explainer: first run answer')).toBeNull()
  })
})
