import { describe, expect, it, vi } from 'vitest'
import {
  connectChat,
  createRun,
  listRuns,
  resumeRun,
  subscribeToRunEvents,
  type FetchLike,
} from './client'
import type { ChatMessage, PipelineEvent, Run } from './types'

/**
 * These tests exercise client.ts without a mocking framework — every method
 * accepts an injectable fetch/EventSource/WebSocket implementation for
 * exactly this purpose (see context/decisions.md, 2026-08-04).
 */

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response
}

describe('REST methods', () => {
  it('listRuns issues a GET to /api/runs and returns the parsed JSON', async () => {
    const runs: Run[] = [
      {
        id: 'run-1',
        competitionName: 'titanic',
        status: 'running',
        currentPhase: 'phase2_research',
        currentIteration: 1,
        bestScore: null,
        createdAt: '2026-08-04T00:00:00Z',
        updatedAt: '2026-08-04T00:00:00Z',
      },
    ]
    const fetchImpl = vi.fn<FetchLike>().mockResolvedValue(jsonResponse(runs))

    const result = await listRuns(fetchImpl)

    expect(result).toEqual(runs)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('/api/runs')
    expect(init).toMatchObject({ method: 'GET' })
    expect((init?.headers as Record<string, string>)['Content-Type']).toBe(
      'application/json',
    )
  })

  it('createRun issues a POST with a JSON-encoded body', async () => {
    const payload = {
      competitionName: 'titanic',
      problemStatement: 'predict survival',
      datasetPath: '/data/titanic',
    }
    const created: Run = {
      id: 'run-2',
      competitionName: payload.competitionName,
      status: 'pending',
      currentPhase: null,
      currentIteration: 0,
      bestScore: null,
      createdAt: '2026-08-04T00:00:00Z',
      updatedAt: '2026-08-04T00:00:00Z',
    }
    const fetchImpl = vi
      .fn<FetchLike>()
      .mockResolvedValue(jsonResponse(created))

    const result = await createRun(payload, fetchImpl)

    expect(result).toEqual(created)
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('/api/runs')
    expect(init?.method).toBe('POST')
    expect(init?.body).toBe(JSON.stringify(payload))
  })

  it('returns undefined for a 204 No Content response', async () => {
    const fetchImpl = vi
      .fn<FetchLike>()
      .mockResolvedValue(jsonResponse(null, 204))

    const result = await resumeRun('run-1', 'looks good', fetchImpl)

    expect(result).toBeUndefined()
  })

  it('throws when the response is not ok', async () => {
    const fetchImpl = vi
      .fn<FetchLike>()
      .mockResolvedValue(jsonResponse({ detail: 'nope' }, 500))

    await expect(listRuns(fetchImpl)).rejects.toThrow(/status 500/)
  })
})

describe('subscribeToRunEvents (SSE)', () => {
  class FakeEventSource {
    static instances: FakeEventSource[] = []
    url: string
    onmessage: ((event: MessageEvent<string>) => void) | null = null
    onerror: ((event: Event) => void) | null = null
    closed = false

    constructor(url: string) {
      this.url = url
      FakeEventSource.instances.push(this)
    }

    close() {
      this.closed = true
    }
  }

  it('parses incoming messages and forwards them to onEvent', () => {
    FakeEventSource.instances = []
    const onEvent = vi.fn()
    subscribeToRunEvents(
      'run-1',
      { onEvent },
      FakeEventSource as unknown as typeof EventSource,
    )

    const source = FakeEventSource.instances[0]
    expect(source.url).toBe('/api/runs/run-1/events')

    const event: PipelineEvent = {
      timestamp: '2026-08-04T00:00:00Z',
      runId: 'run-1',
      iteration: 1,
      phase: 'phase2_research',
      node: 'researcher',
      event: 'end',
    }
    source.onmessage?.({ data: JSON.stringify(event) } as MessageEvent<string>)

    expect(onEvent).toHaveBeenCalledWith(event)
  })

  it('routes a malformed payload to onError instead of throwing', () => {
    FakeEventSource.instances = []
    const onEvent = vi.fn()
    const onError = vi.fn()
    subscribeToRunEvents(
      'run-1',
      { onEvent, onError },
      FakeEventSource as unknown as typeof EventSource,
    )

    const source = FakeEventSource.instances[0]
    expect(() =>
      source.onmessage?.({ data: 'not json' } as MessageEvent<string>),
    ).not.toThrow()

    expect(onEvent).not.toHaveBeenCalled()
    expect(onError).toHaveBeenCalledTimes(1)
  })

  it('forwards connection errors to onError', () => {
    FakeEventSource.instances = []
    const onError = vi.fn()
    subscribeToRunEvents(
      'run-1',
      { onEvent: vi.fn(), onError },
      FakeEventSource as unknown as typeof EventSource,
    )

    const source = FakeEventSource.instances[0]
    const errorEvent = new Event('error')
    source.onerror?.(errorEvent)

    expect(onError).toHaveBeenCalledWith(errorEvent)
  })

  it('closes the underlying connection when unsubscribed', () => {
    FakeEventSource.instances = []
    const unsubscribe = subscribeToRunEvents(
      'run-1',
      { onEvent: vi.fn() },
      FakeEventSource as unknown as typeof EventSource,
    )

    unsubscribe()

    expect(FakeEventSource.instances[0].closed).toBe(true)
  })
})

describe('connectChat (WebSocket)', () => {
  class FakeWebSocket {
    static instances: FakeWebSocket[] = []
    url: string
    sent: string[] = []
    onmessage: ((event: MessageEvent<string>) => void) | null = null
    onerror: ((event: Event) => void) | null = null
    onclose: ((event: CloseEvent) => void) | null = null
    closed = false

    constructor(url: string) {
      this.url = url
      FakeWebSocket.instances.push(this)
    }

    send(data: string) {
      this.sent.push(data)
    }

    close() {
      this.closed = true
    }
  }

  it('sends JSON-encoded content', () => {
    FakeWebSocket.instances = []
    const connection = connectChat(
      'run-1',
      FakeWebSocket as unknown as typeof WebSocket,
    )

    connection.send('hello')

    expect(FakeWebSocket.instances[0].sent).toEqual([
      JSON.stringify({ content: 'hello' }),
    ])
  })

  it('parses incoming messages and forwards them to the registered listener', () => {
    FakeWebSocket.instances = []
    const connection = connectChat(
      'run-1',
      FakeWebSocket as unknown as typeof WebSocket,
    )
    const listener = vi.fn()
    connection.onMessage(listener)

    const message: ChatMessage = {
      id: 'msg-1',
      role: 'assistant',
      content: 'hi there',
      timestamp: '2026-08-04T00:00:00Z',
    }
    FakeWebSocket.instances[0].onmessage?.({
      data: JSON.stringify(message),
    } as MessageEvent<string>)

    expect(listener).toHaveBeenCalledWith(message)
  })

  it('routes a malformed message payload to onError instead of throwing', () => {
    FakeWebSocket.instances = []
    const connection = connectChat(
      'run-1',
      FakeWebSocket as unknown as typeof WebSocket,
    )
    const onError = vi.fn()
    connection.onError(onError)
    connection.onMessage(vi.fn())

    expect(() =>
      FakeWebSocket.instances[0].onmessage?.({
        data: 'not json',
      } as MessageEvent<string>),
    ).not.toThrow()
    expect(onError).toHaveBeenCalledTimes(1)
  })

  it('forwards socket errors to onError', () => {
    FakeWebSocket.instances = []
    const connection = connectChat(
      'run-1',
      FakeWebSocket as unknown as typeof WebSocket,
    )
    const onError = vi.fn()
    connection.onError(onError)

    const errorEvent = new Event('error')
    FakeWebSocket.instances[0].onerror?.(errorEvent)

    expect(onError).toHaveBeenCalledWith(errorEvent)
  })

  it('forwards socket close events to onClose', () => {
    FakeWebSocket.instances = []
    const connection = connectChat(
      'run-1',
      FakeWebSocket as unknown as typeof WebSocket,
    )
    const onClose = vi.fn()
    connection.onClose(onClose)

    const closeEvent = { code: 1000, reason: 'done' } as CloseEvent
    FakeWebSocket.instances[0].onclose?.(closeEvent)

    expect(onClose).toHaveBeenCalledWith(closeEvent)
  })

  it('closes the underlying socket', () => {
    FakeWebSocket.instances = []
    const connection = connectChat(
      'run-1',
      FakeWebSocket as unknown as typeof WebSocket,
    )

    connection.close()

    expect(FakeWebSocket.instances[0].closed).toBe(true)
  })
})
