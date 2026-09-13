/**
 * Client-side API types.
 *
 * `Run` and `PipelineEvent` are reconciled against the real backend
 * (`src/api/`, owned by api-agent) — see docs/api.md's "RunSummary shape"
 * and "GET /api/runs/{id}/events" sections, and
 * context/discoveries/T-034.md / T-035.md for the reconciliation history.
 *
 * PROVISIONAL: the remaining types below (`CreateRunPayload`,
 * `ResumePayload`, `Experiment`) are still best-effort guesses based on
 * design.md's endpoint contract, not yet checked against a real backend
 * response. `Experiment` is a stronger case than the rest: no HTTP response
 * has ever carried this shape — it mirrors `LabState.experiments`
 * (src/state.py) verbatim pending a real endpoint (see
 * context/discoveries/T-040.md).
 *
 * `SubmitResponse` and `MlflowOpenResponse` are reconciled against the real
 * backend (`src/api/models.py::SubmitResponse`/`MlflowUrlResponse`, T-037) —
 * see T-042's reconciliation commit.
 *
 * `ChatClientFrame`/`ChatServerFrame` are reconciled against the live
 * backend (`docs/api.md` § WebSocket / `src/api/routers/chat.py`), not
 * provisional.
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

/**
 * A single completed experiment, as recorded server-side in
 * `LabState.experiments` (src/state.py) — mirrored verbatim, snake_case.
 * PROVISIONAL: no HTTP endpoint currently returns this shape (see
 * context/discoveries/T-040.md).
 */
export interface Experiment {
  id: string
  path: string
  cv_score: number
  iteration: number
  model: string
}

/** A single client -> server frame sent over WS /api/runs/{id}/chat. */
export type ChatClientFrame =
  | { type: 'question'; text: string }
  | { type: 'approve'; feedback: string }
  | { type: 'redirect'; feedback: string }

/** A single server -> client frame received over WS /api/runs/{id}/chat. */
export type ChatServerFrame =
  | { type: 'checkpoint'; phase: string; summary: string }
  | { type: 'answer'; text: string }
  | { type: 'resumed'; run_id: string; status: string }
  | { type: 'error'; detail: string }

/** Payload for POST /api/runs/{id}/resume. */
export interface ResumePayload {
  humanFeedback: string
}

/**
 * Response for POST /api/runs/{id}/submit. `public_score` is `null` when
 * Kaggle has accepted the submission but not yet scored it — the normal
 * state in the seconds-to-minutes window right after submitting, not an
 * error; it must never be coerced to `0`.
 */
export interface SubmitResponse {
  public_score: number | null
  submission_file: string
  message: string | null
}

/** Response for GET /api/mlflow/url. */
export interface MlflowOpenResponse {
  url: string
}
