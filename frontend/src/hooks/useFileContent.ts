import { useEffect, useState } from 'react'
import { getFileContent, type ApiError } from '../api/client'

interface UseFileContentResult {
  content: string | null
  loading: boolean
  error: string | null
}

function isNotFoundError(error: unknown): boolean {
  return (error as Partial<ApiError>)?.status === 404
}

function describeError(error: unknown): string {
  const apiError = error as Partial<ApiError>
  if (apiError?.detail) return apiError.detail
  if (error instanceof Error) return error.message
  return 'Failed to load file content.'
}

/**
 * Fetches GET /api/runs/{id}/files/{path} for the given run + curated path.
 * A `404` (file not generated yet — see files.py) is treated as the existing
 * "no content" empty state, not an error: `content` is set to `null` and
 * `error` stays `null`, mirroring `FileViewer`'s existing degrade-gracefully
 * convention for absent content (retro L-007). Any other failure sets
 * `error` and leaves `content` at `null`.
 */
export function useFileContent(
  runId: string | null,
  path: string | null,
): UseFileContentResult {
  const canFetch = runId !== null && path !== null
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(canFetch)
  const [error, setError] = useState<string | null>(null)

  // Reset on either `runId` or `path` changing — render-time comparison
  // against a composite key, same rationale as useExperiments.ts.
  const key = `${runId ?? ''}::${path ?? ''}`
  const [prevKey, setPrevKey] = useState(key)
  if (key !== prevKey) {
    setPrevKey(key)
    setContent(null)
    setLoading(canFetch)
    setError(null)
  }

  useEffect(() => {
    if (runId === null || path === null) return

    let cancelled = false
    getFileContent(runId, path)
      .then((text) => {
        if (cancelled) return
        setContent(text)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        if (isNotFoundError(err)) {
          setContent(null)
          setError(null)
        } else {
          console.error(`useFileContent: getFileContent(${runId}, ${path}) failed`, err)
          setError(describeError(err))
        }
        setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [runId, path])

  return { content, loading, error }
}
