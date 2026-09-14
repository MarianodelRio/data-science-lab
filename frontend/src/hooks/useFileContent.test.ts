import { renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useFileContent } from './useFileContent'
import * as client from '../api/client'
import type { ApiError } from '../api/client'

vi.mock('../api/client', () => ({
  getFileContent: vi.fn(),
}))

const getFileContent = vi.mocked(client.getFileContent)

function notFoundError(): ApiError {
  const error = new Error('Request failed with status 404') as ApiError
  error.status = 404
  error.detail = 'not found'
  return error
}

beforeEach(() => {
  getFileContent.mockReset()
})

describe('useFileContent', () => {
  it('does not fetch and returns null content when runId or path is null', () => {
    const { result } = renderHook(() => useFileContent(null, 'reports/eda_report.md'))

    expect(result.current.content).toBeNull()
    expect(result.current.loading).toBe(false)
    expect(getFileContent).not.toHaveBeenCalled()
  })

  it('fetches and sets content on success', async () => {
    getFileContent.mockResolvedValue('# EDA Report')

    const { result } = renderHook(() => useFileContent('run-1', 'reports/eda_report.md'))

    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.content).toBe('# EDA Report')
    expect(result.current.error).toBeNull()
    expect(getFileContent).toHaveBeenCalledWith('run-1', 'reports/eda_report.md')
  })

  it('treats a 404 as "not generated yet": content null, error stays null', async () => {
    getFileContent.mockRejectedValue(notFoundError())

    const { result } = renderHook(() => useFileContent('run-1', 'reports/eda_report.md'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.content).toBeNull()
    expect(result.current.error).toBeNull()
  })

  it('sets error (and leaves content null) on a non-404 failure', async () => {
    const error = new Error('Request failed with status 500') as ApiError
    error.status = 500
    error.detail = 'internal error'
    getFileContent.mockRejectedValue(error)

    const { result } = renderHook(() => useFileContent('run-1', 'reports/eda_report.md'))
    await waitFor(() => expect(result.current.loading).toBe(false))

    expect(result.current.content).toBeNull()
    expect(result.current.error).toBe('internal error')
  })

  it('refetches when path changes while runId stays fixed', async () => {
    getFileContent.mockResolvedValueOnce('# EDA')
    const { result, rerender } = renderHook(
      ({ path }) => useFileContent('run-1', path),
      { initialProps: { path: 'reports/eda_report.md' } },
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.content).toBe('# EDA')

    getFileContent.mockResolvedValueOnce('# Final')
    rerender({ path: 'reports/final_report.md' })

    expect(result.current.content).toBeNull()
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.content).toBe('# Final')
    expect(getFileContent).toHaveBeenLastCalledWith('run-1', 'reports/final_report.md')
  })
})
