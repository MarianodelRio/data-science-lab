import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useExperiments } from './useExperiments'
import * as client from '../api/client'
import type { ExperimentsResponse } from '../api/types'

vi.mock('../api/client', () => ({
  getExperiments: vi.fn(),
}))

const getExperiments = vi.mocked(client.getExperiments)

beforeEach(() => {
  getExperiments.mockReset()
})

function makeResponse(overrides: Partial<ExperimentsResponse> = {}): ExperimentsResponse {
  return {
    experiments: [
      { id: 'exp_0', path: 'experiments/exp_0', cv_score: 0.8, iteration: 0, model: 'lgbm' },
    ],
    baseline_score: 0.75,
    best_experiment_path: 'experiments/exp_0',
    ...overrides,
  }
}

describe('useExperiments', () => {
  it('returns undefined fields and no loading when runId is null', () => {
    const { result } = renderHook(() => useExperiments(null))

    expect(result.current.experiments).toBeUndefined()
    expect(result.current.baselineScore).toBeUndefined()
    expect(result.current.loading).toBe(false)
    expect(getExperiments).not.toHaveBeenCalled()
  })

  it('fetches and populates data for a given runId', async () => {
    const response = makeResponse()
    getExperiments.mockResolvedValue(response)

    const { result } = renderHook(() => useExperiments('run-1'))

    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.experiments).toEqual(response.experiments)
    expect(result.current.baselineScore).toBe(0.75)
    expect(result.current.bestExperimentPath).toBe('experiments/exp_0')
    expect(result.current.error).toBeNull()
    expect(getExperiments).toHaveBeenCalledWith('run-1')
  })

  it('keeps baseline_score as null rather than coercing it to 0', async () => {
    getExperiments.mockResolvedValue(makeResponse({ baseline_score: null }))

    const { result } = renderHook(() => useExperiments('run-1'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.baselineScore).toBeNull()
  })

  it('resets and refetches when runId changes', async () => {
    getExperiments.mockResolvedValueOnce(makeResponse({ best_experiment_path: 'experiments/exp_0' }))
    const { result, rerender } = renderHook(({ runId }) => useExperiments(runId), {
      initialProps: { runId: 'run-1' as string | null },
    })
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.bestExperimentPath).toBe('experiments/exp_0')

    getExperiments.mockResolvedValueOnce(makeResponse({ best_experiment_path: 'experiments/exp_5' }))
    rerender({ runId: 'run-2' })

    expect(result.current.experiments).toBeUndefined()
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.bestExperimentPath).toBe('experiments/exp_5')
    expect(getExperiments).toHaveBeenLastCalledWith('run-2')
  })

  it('sets error and leaves data undefined when the fetch fails', async () => {
    getExperiments.mockRejectedValue(new Error('network down'))

    const { result } = renderHook(() => useExperiments('run-1'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.error).toBe('network down')
    expect(result.current.experiments).toBeUndefined()
  })
})
