import { useEffect, useState } from 'react'
import { getExperiments, type ApiError } from '../api/client'
import type { Experiment } from '../api/types'

interface UseExperimentsResult {
  experiments: Experiment[] | undefined
  /** `null` means "no baseline yet" (see ExperimentsTable.tsx) — never coerced. */
  baselineScore: number | null | undefined
  bestExperimentPath: string | undefined
  loading: boolean
  error: string | null
}

function describeError(error: unknown): string {
  const apiError = error as Partial<ApiError>
  if (apiError?.detail) return apiError.detail
  if (error instanceof Error) return error.message
  return 'Failed to load experiments.'
}

/**
 * Fetches GET /api/runs/{id}/experiments for the given run. `ExperimentsTable`
 * itself stays presentational (Architect decision, T-050) — this hook is the
 * container-side data source Layout wires it up with.
 */
export function useExperiments(runId: string | null): UseExperimentsResult {
  const [experiments, setExperiments] = useState<Experiment[] | undefined>(undefined)
  const [baselineScore, setBaselineScore] = useState<number | null | undefined>(undefined)
  const [bestExperimentPath, setBestExperimentPath] = useState<string | undefined>(undefined)
  const [loading, setLoading] = useState(runId !== null)
  const [error, setError] = useState<string | null>(null)

  // Reset all run-scoped state during render when `runId` changes — the same
  // pattern PipelineView.tsx/Chat.tsx use, to avoid an unconditional setState
  // at the top of the effect body (react-hooks/set-state-in-effect).
  const [prevRunId, setPrevRunId] = useState(runId)
  if (runId !== prevRunId) {
    setPrevRunId(runId)
    setExperiments(undefined)
    setBaselineScore(undefined)
    setBestExperimentPath(undefined)
    setLoading(runId !== null)
    setError(null)
  }

  useEffect(() => {
    if (runId === null) return

    let cancelled = false
    getExperiments(runId)
      .then((response) => {
        if (cancelled) return
        setExperiments(response.experiments)
        setBaselineScore(response.baseline_score)
        setBestExperimentPath(response.best_experiment_path)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        console.error(`useExperiments: getExperiments(${runId}) failed`, err)
        setError(describeError(err))
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [runId])

  return { experiments, baselineScore, bestExperimentPath, loading, error }
}
