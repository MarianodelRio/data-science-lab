import { useEffect, useState, type FormEvent } from 'react'
import { createRun, listRuns, type ApiError, type FetchLike } from '../api/client'
import type { CreateRunPayload, Run } from '../api/types'

type ListState = 'loading' | 'ready' | 'error'

interface SidebarProps {
  selectedRunId: string | null
  onSelectRun: (runId: string) => void
  /** Injected for tests (same pattern as ActionBar.tsx) — defaults to `fetch`. */
  fetchImpl?: FetchLike
}

/** Builds the create-run body, omitting `max_iterations` when the field is left blank. */
function buildCreateRunPayload(
  competitionName: string,
  workspacePath: string,
  maxIterationsInput: string,
): CreateRunPayload {
  const trimmed = maxIterationsInput.trim()
  if (trimmed === '') {
    return { competition_name: competitionName, workspace_path: workspacePath }
  }
  return {
    competition_name: competitionName,
    workspace_path: workspacePath,
    max_iterations: Number(trimmed),
  }
}

function describeError(error: unknown): string {
  const apiError = error as Partial<ApiError>
  if (apiError?.detail) return apiError.detail
  if (error instanceof Error) return error.message
  return 'Failed to create run.'
}

function RunList({
  runs,
  selectedRunId,
  onSelectRun,
}: {
  runs: Run[]
  selectedRunId: string | null
  onSelectRun: (runId: string) => void
}) {
  if (runs.length === 0) {
    return <p>No runs yet.</p>
  }
  return (
    <ul aria-label="Run list">
      {runs.map((run) => (
        <li key={run.run_id}>
          <button
            type="button"
            onClick={() => onSelectRun(run.run_id)}
            aria-current={run.run_id === selectedRunId ? 'true' : undefined}
          >
            {run.competition_name} — {run.status}
          </button>
        </li>
      ))}
    </ul>
  )
}

/**
 * Runs sidebar: lists existing runs (self-fetching, unlike the presentational
 * ExperimentsTable/FileViewer) and offers a minimal create-run form. Selecting
 * an existing run, or creating a new one, is reported to the parent via
 * `onSelectRun` — this component owns no selection state of its own.
 */
export function Sidebar({ selectedRunId, onSelectRun, fetchImpl }: SidebarProps) {
  const [runs, setRuns] = useState<Run[]>([])
  const [listState, setListState] = useState<ListState>('loading')
  const [competitionName, setCompetitionName] = useState('')
  const [workspacePath, setWorkspacePath] = useState('')
  const [maxIterations, setMaxIterations] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // No synchronous setState here (react-hooks/set-state-in-effect): the
    // initial `listState` is already 'loading', which covers the mount
    // case; `fetchImpl` changing after mount is a test-only scenario, not a
    // real runtime one, so a mid-flight state reset is not needed here.
    listRuns(fetchImpl)
      .then((result) => {
        if (cancelled) return
        setRuns(result)
        setListState('ready')
      })
      .catch((error: unknown) => {
        if (cancelled) return
        console.error('Sidebar: failed to fetch runs', error)
        setListState('error')
      })
    return () => {
      cancelled = true
    }
  }, [fetchImpl])

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setCreating(true)
    setCreateError(null)

    let runId: string
    try {
      const payload = buildCreateRunPayload(competitionName, workspacePath, maxIterations)
      ;({ run_id: runId } = await createRun(payload, fetchImpl))
    } catch (error) {
      // createRun itself failed — no run was created. Report it and leave
      // the form populated so the user can fix and retry.
      console.error('Sidebar: failed to create run', error)
      setCreateError(describeError(error))
      setCreating(false)
      return
    }

    // createRun succeeded: the run exists now. Clear the form immediately so
    // a resubmit can't accidentally create a duplicate, and select the new
    // run using the id we already have — neither depends on the refresh below.
    setCompetitionName('')
    setWorkspacePath('')
    setMaxIterations('')
    onSelectRun(runId)

    try {
      // Do not construct a Run from the POST response ({run_id, status} only)
      // — refresh from the list endpoint instead, which returns the full shape.
      const refreshed = await listRuns(fetchImpl)
      setRuns(refreshed)
      setListState('ready')
    } catch (error) {
      // The run was already created successfully — a failure here is a list
      // refresh problem, not a run-creation failure, so it must not surface
      // as "Failed to create run." The list will catch up on the next mount
      // or manual refresh; log for diagnosis only.
      console.error('Sidebar: run created, but refreshing the run list failed', error)
    }

    setCreating(false)
  }

  return (
    <aside aria-label="Runs">
      <h2>Runs</h2>
      {listState === 'loading' && <p role="status">Loading runs…</p>}
      {listState === 'error' && <p role="alert">Failed to load runs.</p>}
      {listState === 'ready' && (
        <RunList runs={runs} selectedRunId={selectedRunId} onSelectRun={onSelectRun} />
      )}
      <form onSubmit={handleCreate}>
        <h3>New run</h3>
        <label htmlFor="sidebar-competition-name">Competition name</label>
        <input
          id="sidebar-competition-name"
          value={competitionName}
          onChange={(e) => setCompetitionName(e.target.value)}
          required
        />
        <label htmlFor="sidebar-workspace-path">Workspace path</label>
        <input
          id="sidebar-workspace-path"
          value={workspacePath}
          onChange={(e) => setWorkspacePath(e.target.value)}
          required
        />
        <label htmlFor="sidebar-max-iterations">Max iterations (optional)</label>
        <input
          id="sidebar-max-iterations"
          type="number"
          value={maxIterations}
          onChange={(e) => setMaxIterations(e.target.value)}
        />
        <button type="submit" disabled={creating}>
          {creating ? 'Creating…' : 'Create run'}
        </button>
        {createError && <p role="alert">{createError}</p>}
      </form>
    </aside>
  )
}
