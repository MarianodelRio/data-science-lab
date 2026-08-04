/**
 * Client-side API types.
 *
 * PROVISIONAL: design.md documents the endpoint contract (method, path,
 * protocol, purpose) but not the JSON response schemas — the backend
 * (`src/api/`, owned by api-agent) has not been implemented yet. These
 * shapes are best-effort guesses based on the pipeline's phase/state model
 * described in design.md. They must be reconciled against the real backend
 * responses once the FastAPI routes land (see context/discoveries.md).
 */

/** High-level lifecycle status of a pipeline run. */
export type RunStatus =
  'pending' | 'running' | 'interrupted' | 'completed' | 'failed'

/** Summary of a single pipeline run, as returned by GET/POST /api/runs. */
export interface Run {
  id: string
  competitionName: string
  status: RunStatus
  currentPhase: string | null
  currentIteration: number
  bestScore: number | null
  createdAt: string
  updatedAt: string
}

/** Payload for POST /api/runs. */
export interface CreateRunPayload {
  competitionName: string
  problemStatement: string
  datasetPath: string
}

/** A single event streamed over GET /api/runs/{id}/events (SSE). */
export interface PipelineEvent {
  timestamp: string
  runId: string
  iteration: number
  phase: string
  node: string
  event: 'start' | 'end' | 'error'
  durationMs?: number
  tokensIn?: number
  tokensOut?: number
  model?: string
  outputSummary?: string
}

/** A single chat message exchanged over WS /api/runs/{id}/chat. */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

/** Payload for POST /api/runs/{id}/resume. */
export interface ResumePayload {
  humanFeedback: string
}

/** Response for POST /api/runs/{id}/submit. */
export interface SubmitResponse {
  submitted: boolean
  kaggleSubmissionId?: string
  message?: string
}

/** Response for POST /api/mlflow/open. */
export interface MlflowOpenResponse {
  url: string
}
