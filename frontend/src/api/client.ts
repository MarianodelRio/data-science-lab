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
  ChatMessage,
  CreateRunPayload,
  MlflowOpenResponse,
  PipelineEvent,
  Run,
  SubmitResponse,
} from './types'

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

export type FetchLike = typeof fetch

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
    throw new Error(`Request to ${path} failed with status ${response.status}`)
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
): Promise<Run> {
  return request<Run>(
    '/api/runs',
    { method: 'POST', body: JSON.stringify(payload) },
    fetchImpl,
  )
}

/** Handlers for a live SSE subscription created by subscribeToRunEvents. */
export interface RunEventHandlers {
  onEvent: (event: PipelineEvent) => void
  onError?: (error: Event) => void
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
    handlers.onEvent(JSON.parse(message.data) as PipelineEvent)
  }

  if (handlers.onError) {
    source.onerror = handlers.onError
  }

  return () => source.close()
}

/** A live WS connection returned by connectChat. */
export interface ChatConnection {
  send: (content: string) => void
  onMessage: (listener: (message: ChatMessage) => void) => void
  close: () => void
}

/**
 * WS /api/runs/{id}/chat — bidirectional chat with the explainer agent.
 * No reconnect logic here by design — that is a future task's job.
 */
export function connectChat(
  runId: string,
  WebSocketImpl: typeof WebSocket = WebSocket,
): ChatConnection {
  const wsBase = API_BASE.replace(/^http/, 'ws')
  const socket = new WebSocketImpl(
    `${wsBase}/api/runs/${encodeURIComponent(runId)}/chat`,
  )

  return {
    send: (content: string) => socket.send(JSON.stringify({ content })),
    onMessage: (listener: (message: ChatMessage) => void) => {
      socket.onmessage = (message: MessageEvent<string>) => {
        listener(JSON.parse(message.data) as ChatMessage)
      }
    },
    close: () => socket.close(),
  }
}

/** POST /api/runs/{id}/resume — submit human_feedback, resume from interrupt. */
export function resumeRun(
  runId: string,
  feedback: string,
  fetchImpl: FetchLike = fetch,
): Promise<void> {
  return request<void>(
    `/api/runs/${encodeURIComponent(runId)}/resume`,
    { method: 'POST', body: JSON.stringify({ humanFeedback: feedback }) },
    fetchImpl,
  )
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

/** POST /api/mlflow/open — launch `mlflow ui` subprocess, return its URL. */
export function openMlflow(
  fetchImpl: FetchLike = fetch,
): Promise<MlflowOpenResponse> {
  return request<MlflowOpenResponse>(
    '/api/mlflow/open',
    { method: 'POST' },
    fetchImpl,
  )
}
