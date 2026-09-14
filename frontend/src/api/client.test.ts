import { describe, expect, it, vi } from 'vitest'
import {
  connectChat,
  createRun,
  getExperiments,
  getFileContent,
  getRun,
  listRuns,
  openMlflow,
  resumeRun,
  submitRun,
  subscribeToRunEvents,
  type ApiError,
  type FetchLike,
} from './client'
import type {
  ChatClientFrame,
  ChatServerFrame,
  CreateRunResponse,
  ExperimentsResponse,
  MlflowOpenResponse,
  PipelineEvent,
  ResumeResponse,
  Run,
  SubmitResponse,
} from './types'

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
        run_id: 'run-1',
        competition_name: 'titanic',
        workspace_path: '/workspaces/titanic',
        status: 'running',
        phase: 'phase2_research',
        current_iteration: 1,
        best_score: null,
        created_at: '2026-08-04T00:00:00Z',
        updated_at: '2026-08-04T00:00:00Z',
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

  it('createRun issues a POST with a snake_case JSON-encoded body and returns {run_id, status}', async () => {
    const payload = {
      competition_name: 'titanic',
      workspace_path: '/workspaces/titanic',
      max_iterations: 5,
    }
    const created: CreateRunResponse = { run_id: 'run-2', status: 'pending' }
    const fetchImpl = vi
      .fn<FetchLike>()
      .mockResolvedValue(jsonResponse(created, 201))

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

  it('submitRun issues a POST to /api/runs/{id}/submit and returns the parsed SubmitResponse', async () => {
    const submitted: SubmitResponse = {
      public_score: 42.5,
      submission_file: 'submission.csv',
      message: null,
    }
    const fetchImpl = vi
      .fn<FetchLike>()
      .mockResolvedValue(jsonResponse(submitted))

    const result = await submitRun('run-1', fetchImpl)

    expect(result).toEqual(submitted)
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('/api/runs/run-1/submit')
    expect(init).toMatchObject({ method: 'POST' })
  })

  it('openMlflow issues a GET to /api/mlflow/url and returns the parsed JSON', async () => {
    const mlflow: MlflowOpenResponse = { url: 'http://localhost:5000' }
    const fetchImpl = vi.fn<FetchLike>().mockResolvedValue(jsonResponse(mlflow))

    const result = await openMlflow(fetchImpl)

    expect(result).toEqual(mlflow)
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('/api/mlflow/url')
    expect(init).toMatchObject({ method: 'GET' })
  })

  it('getRun issues a GET to /api/runs/{id} and returns the parsed JSON', async () => {
    const run: Run = {
      run_id: 'run-1',
      competition_name: 'titanic',
      workspace_path: '/workspaces/titanic',
      status: 'running',
      phase: 'phase2_research',
      current_iteration: 1,
      best_score: null,
      created_at: '2026-08-04T00:00:00Z',
      updated_at: '2026-08-04T00:00:00Z',
    }
    const fetchImpl = vi.fn<FetchLike>().mockResolvedValue(jsonResponse(run))

    const result = await getRun('run-1', fetchImpl)

    expect(result).toEqual(run)
    expect(fetchImpl).toHaveBeenCalledTimes(1)
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('/api/runs/run-1')
    expect(init).toMatchObject({ method: 'GET' })
  })

  it('resumeRun sends exactly {feedback} and returns {run_id, status}', async () => {
    const response: ResumeResponse = { run_id: 'run-1', status: 'running' }
    const fetchImpl = vi.fn<FetchLike>().mockResolvedValue(jsonResponse(response))

    const result = await resumeRun('run-1', 'looks good', fetchImpl)

    expect(result).toEqual(response)
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('/api/runs/run-1/resume')
    expect(init?.body).toBe(JSON.stringify({ feedback: 'looks good' }))
  })

  it('getExperiments issues a GET to /api/runs/{id}/experiments and round-trips a null baseline_score', async () => {
    const response: ExperimentsResponse = {
      experiments: [
        { id: 'exp_0', path: 'experiments/exp_0', cv_score: 0.8, iteration: 0, model: 'lgbm' },
      ],
      baseline_score: null,
      best_experiment_path: '',
    }
    const fetchImpl = vi.fn<FetchLike>().mockResolvedValue(jsonResponse(response))

    const result = await getExperiments('run-1', fetchImpl)

    expect(result).toEqual(response)
    expect(result.baseline_score).toBeNull()
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('/api/runs/run-1/experiments')
    expect(init).toMatchObject({ method: 'GET' })
  })
})

describe('getFileContent', () => {
  it('returns the raw text body and never calls response.json()', async () => {
    const jsonSpy = vi.fn()
    const fetchImpl = vi.fn<FetchLike>().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve('# markdown'),
      json: jsonSpy,
    } as unknown as Response)

    const result = await getFileContent('run-1', 'reports/eda_report.md', fetchImpl)

    expect(result).toBe('# markdown')
    expect(jsonSpy).not.toHaveBeenCalled()
    const [url] = fetchImpl.mock.calls[0]
    expect(url).toBe('/api/runs/run-1/files/reports/eda_report.md')
  })

  it('rejects with a .status === 404 error on a not-found response', async () => {
    const fetchImpl = vi.fn<FetchLike>().mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: 'not found' }),
    } as unknown as Response)

    try {
      await getFileContent('run-1', 'reports/eda_report.md', fetchImpl)
      expect.unreachable('getFileContent should have thrown')
    } catch (error) {
      const apiError = error as ApiError
      expect(apiError.status).toBe(404)
      expect(apiError.detail).toBe('not found')
    }
  })
})

describe('request<T>() error-detail attachment', () => {
  function errorResponse(status: number, body: unknown): Response {
    return {
      ok: false,
      status,
      json: () => Promise.resolve(body),
    } as Response
  }

  it('attaches status and the backend detail to the thrown error, without changing its message', async () => {
    const fetchImpl = vi
      .fn<FetchLike>()
      .mockResolvedValue(
        errorResponse(409, { detail: 'no best experiment yet' }),
      )

    try {
      await submitRun('run-1', fetchImpl)
      expect.unreachable('submitRun should have thrown')
    } catch (error) {
      const apiError = error as ApiError
      expect(apiError.message).toBe(
        'Request to /api/runs/run-1/submit failed with status 409',
      )
      expect(apiError.status).toBe(409)
      expect(apiError.detail).toBe('no best experiment yet')
    }
  })

  it('leaves detail undefined and throws only once when the error body is not valid JSON', async () => {
    const fetchImpl = vi.fn<FetchLike>().mockResolvedValue({
      ok: false,
      status: 502,
      json: () =>
        Promise.reject(new SyntaxError('Unexpected end of JSON input')),
    } as unknown as Response)

    await expect(submitRun('run-1', fetchImpl)).rejects.toThrow(
      'Request to /api/runs/run-1/submit failed with status 502',
    )
  })

  it('leaves detail undefined when the JSON error body has no detail key', async () => {
    const fetchImpl = vi
      .fn<FetchLike>()
      .mockResolvedValue(errorResponse(500, {}))

    try {
      await submitRun('run-1', fetchImpl)
      expect.unreachable('submitRun should have thrown')
    } catch (error) {
      const apiError = error as ApiError
      expect(apiError.status).toBe(500)
      expect(apiError.detail).toBeUndefined()
    }
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
      run_id: 'run-1',
      iteration: 1,
      phase: 'phase2_research',
      node: 'researcher',
      event: 'end',
      duration_ms: null,
      output_summary: null,
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
    onopen: (() => void) | null = null
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

  it('sends a JSON-encoded client frame', () => {
    FakeWebSocket.instances = []
    const connection = connectChat(
      'run-1',
      FakeWebSocket as unknown as typeof WebSocket,
    )

    const frames: ChatClientFrame[] = [
      { type: 'question', text: 'hello' },
      { type: 'approve', feedback: 'looks good' },
      { type: 'redirect', feedback: 'try again' },
    ]
    for (const frame of frames) {
      connection.send(frame)
    }

    expect(FakeWebSocket.instances[0].sent).toEqual(
      frames.map((frame) => JSON.stringify(frame)),
    )
  })

  it('parses incoming server frames and forwards them to the registered listener', () => {
    FakeWebSocket.instances = []
    const connection = connectChat(
      'run-1',
      FakeWebSocket as unknown as typeof WebSocket,
    )
    const listener = vi.fn()
    connection.onMessage(listener)

    const frame: ChatServerFrame = {
      type: 'checkpoint',
      phase: 'phase3_baseline',
      summary: '',
    }
    FakeWebSocket.instances[0].onmessage?.({
      data: JSON.stringify(frame),
    } as MessageEvent<string>)

    expect(listener).toHaveBeenCalledWith(frame)
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

  it('forwards socket open events to the registered onOpen listener', () => {
    FakeWebSocket.instances = []
    const connection = connectChat(
      'run-1',
      FakeWebSocket as unknown as typeof WebSocket,
    )
    const onOpen = vi.fn()
    connection.onOpen(onOpen)

    FakeWebSocket.instances[0].onopen?.()

    expect(onOpen).toHaveBeenCalledTimes(1)
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
