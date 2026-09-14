/**
 * Typed backend API client.
 *
 * This is the single point of contact with the backend — components must
 * never call `fetch`/`EventSource`/`WebSocket` directly, they go through the
 * methods exported here. See docs/api.md for the endpoint reference.
 *
 * Each REST method accepts an optional injected `fetch` implementation
 * (defaulting to the global one) so call sites can be unit tested by
 * passing a stub function, without pulling in a mocking framework.
 */
import type {
  ChatClientFrame,
  ChatServerFrame,
  CreateRunPayload,
  CreateRunResponse,
  ExperimentsResponse,
  MlflowOpenResponse,
  PipelineEvent,
  ResumeResponse,
  Run,
  SubmitResponse,
} from './types'

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

export type FetchLike = typeof fetch

/**
 * Thrown by `request<T>()` on a non-ok response. Stays a real `Error` — with
 * the same `.message` format existing call sites already assert on — with
 * `status`/`detail` attached as additive properties rather than replacing
 * the thrown value's type.
 */
export type ApiError = Error & { status: number; detail?: string }

/**
 * Best-effort extraction of FastAPI's `{"detail": "..."}` error body shape
 * (the default for a raised `HTTPException(status_code=..., detail=...)`,
 * confirmed against every `src/api/routers/*.py` call site as of T-042).
 * Returns `undefined` — rather than throwing a second, more confusing error
 * — when the body is empty, not JSON, or JSON without a string `detail`.
 */
async function extractErrorDetail(
  response: Response,
): Promise<string | undefined> {
  try {
    const body: unknown = await response.json()
    if (
      body &&
      typeof body === 'object' &&
      typeof (body as { detail?: unknown }).detail === 'string'
    ) {
      return (body as { detail: string }).detail
    }
    return undefined
  } catch {
    return undefined
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  fetchImpl: FetchLike = fetch,
): Promise<T> {
  const response = await fetchImpl(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init.headers },
    ...init,
  })

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    const error = new Error(
      `Request to ${path} failed with status ${response.status}`,
    ) as ApiError
    error.status = response.status
    error.detail = detail
    throw error
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

/** GET /api/runs — list all runs. */
export function listRuns(fetchImpl: FetchLike = fetch): Promise<Run[]> {
  return request<Run[]>('/api/runs', { method: 'GET' }, fetchImpl)
}

/** POST /api/runs — create and start a new run. */
export function createRun(
  payload: CreateRunPayload,
  fetchImpl: FetchLike = fetch,
): Promise<CreateRunResponse> {
  return request<CreateRunResponse>(
    '/api/runs',
    { method: 'POST', body: JSON.stringify(payload) },
    fetchImpl,
  )
}

/** GET /api/runs/{id} — fetch a single run's current status. */
export function getRun(
  runId: string,
  fetchImpl: FetchLike = fetch,
): Promise<Run> {
  return request<Run>(
    `/api/runs/${encodeURIComponent(runId)}`,
    { method: 'GET' },
    fetchImpl,
  )
}

/** Handlers for a live SSE subscription created by subscribeToRunEvents. */
export interface RunEventHandlers {
  onEvent: (event: PipelineEvent) => void
  /** Fired on a connection error, or when a message payload fails to parse. */
  onError?: (error: Event | Error) => void
}

/**
 * GET /api/runs/{id}/events — stream pipeline events in real time (SSE).
 * Returns an unsubscribe function that closes the underlying EventSource.
 */
export function subscribeToRunEvents(
  runId: string,
  handlers: RunEventHandlers,
  EventSourceImpl: typeof EventSource = EventSource,
): () => void {
  const source = new EventSourceImpl(
    `${API_BASE}/api/runs/${encodeURIComponent(runId)}/events`,
  )

  source.onmessage = (message: MessageEvent<string>) => {
    try {
      handlers.onEvent(JSON.parse(message.data) as PipelineEvent)
    } catch (error) {
      handlers.onError?.(error as Error)
    }
  }

  if (handlers.onError) {
    source.onerror = handlers.onError
  }

  return () => source.close()
}

/** A live WS connection returned by connectChat. */
export interface ChatConnection {
  send: (frame: ChatClientFrame) => void
  onMessage: (listener: (frame: ChatServerFrame) => void) => void
  /** Fired once the underlying socket's handshake completes (native `onopen`). */
  onOpen: (listener: () => void) => void
  onError: (listener: (error: Event | Error) => void) => void
  onClose: (listener: (event: CloseEvent) => void) => void
  close: () => void
}

/**
 * WS /api/runs/{id}/chat — bidirectional chat with the explainer agent.
 * No reconnect logic here by design — that is a future task's job; onClose
 * only surfaces the event so a future caller can decide what to do.
 * `onOpen` surfaces the native handshake completion so callers can gate
 * `send` on the socket actually being open (`WebSocket.send()` throws
 * `InvalidStateError` before `readyState === OPEN`).
 */
export function connectChat(
  runId: string,
  WebSocketImpl: typeof WebSocket = WebSocket,
): ChatConnection {
  const wsBase = API_BASE.replace(/^http/, 'ws')
  const socket = new WebSocketImpl(
    `${wsBase}/api/runs/${encodeURIComponent(runId)}/chat`,
  )

  let errorListener: ((error: Event | Error) => void) | undefined

  socket.onerror = (event: Event) => {
    errorListener?.(event)
  }

  return {
    send: (frame: ChatClientFrame) => socket.send(JSON.stringify(frame)),
    onMessage: (listener: (frame: ChatServerFrame) => void) => {
      socket.onmessage = (message: MessageEvent<string>) => {
        try {
          listener(JSON.parse(message.data) as ChatServerFrame)
        } catch (error) {
          errorListener?.(error as Error)
        }
      }
    },
    onOpen: (listener: () => void) => {
      socket.onopen = () => listener()
    },
    onError: (listener: (error: Event | Error) => void) => {
      errorListener = listener
    },
    onClose: (listener: (event: CloseEvent) => void) => {
      socket.onclose = listener
    },
    close: () => socket.close(),
  }
}

/** POST /api/runs/{id}/resume — submit feedback, resume from interrupt. */
export function resumeRun(
  runId: string,
  feedback: string,
  fetchImpl: FetchLike = fetch,
): Promise<ResumeResponse> {
  return request<ResumeResponse>(
    `/api/runs/${encodeURIComponent(runId)}/resume`,
    { method: 'POST', body: JSON.stringify({ feedback }) },
    fetchImpl,
  )
}

/** GET /api/runs/{id}/experiments — the run's experiments plus baseline score. */
export function getExperiments(
  runId: string,
  fetchImpl: FetchLike = fetch,
): Promise<ExperimentsResponse> {
  return request<ExperimentsResponse>(
    `/api/runs/${encodeURIComponent(runId)}/experiments`,
    { method: 'GET' },
    fetchImpl,
  )
}

/**
 * GET /api/runs/{id}/files/{path} — a workspace file's raw text content, for
 * `FileViewer`. Bypasses `request<T>()`: the response is `text/plain`, not
 * JSON, so it must not go through `.json()`. On failure, mirrors
 * `request<T>()`'s error shape (a real `Error` with `.status`/`.detail`
 * attached) so callers can branch on `.status` identically to every other
 * client call — in particular, the caller treats `.status === 404` as "not
 * generated yet," not a genuine error.
 *
 * `path` is not URI-encoded: the backend route (`{path:path}`,
 * `src/api/routers/files.py`) is a FastAPI/Starlette "path" converter that
 * matches literal `/`-containing values directly (e.g. `reports/eda_report.md`)
 * — encoding the slashes would break route matching instead of fixing it.
 */
export async function getFileContent(
  runId: string,
  path: string,
  fetchImpl: FetchLike = fetch,
): Promise<string> {
  const response = await fetchImpl(
    `${API_BASE}/api/runs/${encodeURIComponent(runId)}/files/${path}`,
    { method: 'GET' },
  )

  if (!response.ok) {
    const detail = await extractErrorDetail(response)
    const error = new Error(
      `Request to /api/runs/${runId}/files/${path} failed with status ${response.status}`,
    ) as ApiError
    error.status = response.status
    error.detail = detail
    throw error
  }

  return response.text()
}

/** POST /api/runs/{id}/submit — trigger Kaggle submission. */
export function submitRun(
  runId: string,
  fetchImpl: FetchLike = fetch,
): Promise<SubmitResponse> {
  return request<SubmitResponse>(
    `/api/runs/${encodeURIComponent(runId)}/submit`,
    { method: 'POST' },
    fetchImpl,
  )
}

/** GET /api/mlflow/url — resolved MLflow UI URL (fixed at app construction). */
export function openMlflow(
  fetchImpl: FetchLike = fetch,
): Promise<MlflowOpenResponse> {
  return request<MlflowOpenResponse>(
    '/api/mlflow/url',
    { method: 'GET' },
    fetchImpl,
  )
}
