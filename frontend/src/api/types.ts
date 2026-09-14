/**
 * Client-side API types.
 *
 * All types below are reconciled against the live backend (`src/api/`, owned
 * by api-agent) — see `docs/api.md` for the endpoint reference. `Run` and
 * `PipelineEvent` were reconciled in earlier tasks (T-034/T-035); `Experiment`,
 * `CreateRunPayload`/`CreateRunResponse`, `ResumePayload`/`ResumeResponse`,
 * `ExperimentsResponse`, `SubmitResponse`, `MlflowOpenResponse`, and
 * `ChatClientFrame`/`ChatServerFrame` were reconciled in T-037/T-041/T-042/T-050.
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
  competition_name: string
  workspace_path: string
  max_iterations?: number
}

/** Response for POST /api/runs — a 201 with only the new run's id and status. */
export interface CreateRunResponse {
  run_id: string
  status: RunStatus
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
 * `id` is unique by construction (verified against `src/nodes/llm/coder.py`,
 * the sole writer, which appends exactly this shape per experiment).
 */
export interface Experiment {
  id: string
  path: string
  cv_score: number
  iteration: number
  model: string
}

/** Response for GET /api/runs/{id}/experiments. */
export interface ExperimentsResponse {
  experiments: Experiment[]
  /** `null` until the run's baseline has actually run — never the raw `0.0` seed. */
  baseline_score: number | null
  /** `""` for a run with no improved experiment yet. */
  best_experiment_path: string
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
  feedback: string
}

/** Response for POST /api/runs/{id}/resume. */
export interface ResumeResponse {
  run_id: string
  status: RunStatus
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
