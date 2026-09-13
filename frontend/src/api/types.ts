/**
 * Client-side API types.
 *
 * `Run` and `PipelineEvent` are reconciled against the real backend
 * (`src/api/`, owned by api-agent) — see docs/api.md's "RunSummary shape"
 * and "GET /api/runs/{id}/events" sections, and
 * context/discoveries/T-034.md / T-035.md for the reconciliation history.
 *
 * PROVISIONAL: the remaining types below (`ChatMessage`, `CreateRunPayload`,
 * `ResumePayload`, `SubmitResponse`, `MlflowOpenResponse`) are still
 * best-effort guesses based on design.md's endpoint contract, not yet
 * checked against a real backend response.
 */

/** High-level lifecycle status of a pipeline run. */
export type RunStatus =
  'pending' | 'running' | 'interrupted' | 'completed' | 'failed'

/** Summary of a single pipeline run, as returned by GET/POST /api/runs. */
export interface Run {
  run_id: string
  competition_name: string
  workspace_path: string
  status: RunStatus
  /** Always a string — `""` before any phase has run, never `null`. */
  phase: string
  current_iteration: number
  best_score: number | null
  created_at: string
  updated_at: string
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
  run_id: string
  iteration: number | null
  phase: string | null
  node: string
  event: 'start' | 'end'
  /** `null` on `start`, populated on the matching `end`. */
  duration_ms: number | null
  /** `null` on `start`, populated on the matching `end`. */
  output_summary: string | null
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
