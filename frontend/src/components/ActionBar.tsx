import { useEffect, useState } from 'react'
import {
  openMlflow,
  submitRun,
  type ApiError,
  type FetchLike,
} from '../api/client'
import type { SubmitResponse } from '../api/types'

type SubmitState = 'idle' | 'loading' | 'success' | 'error'

interface SubmitError {
  status?: number
  detail?: string
  message: string
}

function toSubmitError(error: unknown): SubmitError {
  const apiError = error as Partial<ApiError>
  return {
    status: apiError?.status,
    detail: apiError?.detail,
    message: apiError instanceof Error ? apiError.message : 'Submission failed',
  }
}

function SubmitResult({ result }: { result: SubmitResponse }) {
  if (result.public_score === null) {
    return (
      <p role="status">Accepted — not yet scored. {result.message ?? ''}</p>
    )
  }
  return <p role="status">Public score: {result.public_score}</p>
}

function SubmitFailure({ error }: { error: SubmitError }) {
  const text = error.detail ?? error.message
  return (
    <p role="alert">
      {error.status ? `Error ${error.status}: ` : 'Error: '}
      {text}
    </p>
  )
}

/**
 * Kaggle submission + MLflow launch, wired against `client.ts`. `fetchImpl`
 * is forwarded to both `submitRun`/`openMlflow` so tests can stub it, the
 * same injected-dependency pattern `Chat.test.tsx` uses for its WebSocket
 * stub — no mocking framework, no network in unit tests.
 */
export function ActionBar({
  runId,
  fetchImpl,
}: {
  runId?: string
  fetchImpl?: FetchLike
}) {
  const [mlflowUrl, setMlflowUrl] = useState<string | null>(null)
  const [submitState, setSubmitState] = useState<SubmitState>('idle')
  const [submitResult, setSubmitResult] = useState<SubmitResponse | null>(null)
  const [submitError, setSubmitError] = useState<SubmitError | null>(null)

  // Reset submit state during render when `runId` changes — same pattern as
  // useExperiments.ts/useFileContent.ts, to avoid an unconditional setState
  // at the top of an effect (react-hooks/set-state-in-effect). ActionBar now
  // stays mounted across run switches (T-050), so without this a stale
  // result/error from a previous run's submission would keep showing under
  // the newly selected run. `mlflowUrl` is intentionally not reset here — it
  // is fetched once on mount and does not vary per run.
  const [prevRunId, setPrevRunId] = useState(runId)
  if (runId !== prevRunId) {
    setPrevRunId(runId)
    setSubmitState('idle')
    setSubmitResult(null)
    setSubmitError(null)
  }

  // Fetched once on mount — never inside the MLflow click handler — so that
  // handler can call `window.open` as the first synchronous statement,
  // avoiding popup-blocker rejection of an `open()` call that follows an
  // `await`.
  useEffect(() => {
    let cancelled = false
    openMlflow(fetchImpl)
      .then((response) => {
        if (!cancelled) setMlflowUrl(response.url)
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          console.error('ActionBar: failed to fetch the MLflow URL', error)
        }
      })
    return () => {
      cancelled = true
    }
  }, [fetchImpl])

  const handleSubmit = async () => {
    if (!runId) return
    setSubmitState('loading')
    setSubmitError(null)
    try {
      const result = await submitRun(runId, fetchImpl)
      setSubmitResult(result)
      setSubmitState('success')
    } catch (error) {
      setSubmitError(toSubmitError(error))
      setSubmitState('error')
    }
  }

  const handleOpenMlflow = () => {
    if (!mlflowUrl) return
    window.open(mlflowUrl, '_blank', 'noopener,noreferrer')
  }

  return (
    <div>
      <h2>Actions</h2>
      <button
        type="button"
        onClick={handleSubmit}
        disabled={!runId || submitState === 'loading'}
      >
        Submit to Kaggle
      </button>
      {submitState === 'loading' && <p role="status">Submitting…</p>}
      {submitState === 'success' && submitResult && (
        <SubmitResult result={submitResult} />
      )}
      {submitState === 'error' && submitError && (
        <SubmitFailure error={submitError} />
      )}
      <button type="button" onClick={handleOpenMlflow} disabled={!mlflowUrl}>
        Open MLflow
      </button>
    </div>
  )
}
