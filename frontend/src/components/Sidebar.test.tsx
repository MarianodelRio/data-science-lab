import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Sidebar } from './Sidebar'
import type { FetchLike } from '../api/client'
import type { Run } from '../api/types'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response
}

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    run_id: 'run-1',
    competition_name: 'titanic',
    workspace_path: '/workspaces/titanic',
    status: 'running',
    phase: 'phase2_research',
    current_iteration: 1,
    best_score: null,
    created_at: '2026-09-14T00:00:00Z',
    updated_at: '2026-09-14T00:00:00Z',
    ...overrides,
  }
}

/**
 * Routes a stubbed fetch by method + URL. `listRunsResponses` is consumed
 * in order across successive GET /api/runs calls, so a test can assert the
 * post-create refresh sees a different list than the initial mount fetch.
 */
function stubFetch({
  listRunsResponses,
  create,
}: {
  listRunsResponses: Run[][]
  create?: () => Response | Promise<Response>
}): FetchLike {
  let listCallIndex = 0
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url === '/api/runs' && method === 'GET') {
      const body = listRunsResponses[Math.min(listCallIndex, listRunsResponses.length - 1)]
      listCallIndex += 1
      return Promise.resolve(jsonResponse(body))
    }
    if (url === '/api/runs' && method === 'POST' && create) {
      return Promise.resolve(create())
    }
    return Promise.reject(new Error(`unexpected fetch to ${method} ${url}`))
  }) as unknown as FetchLike
}

describe('Sidebar — run list', () => {
  it('renders a distinct loading state, then the fetched run list', async () => {
    const runs = [makeRun({ run_id: 'run-1', competition_name: 'titanic' })]
    const fetchImpl = stubFetch({ listRunsResponses: [runs] })

    render(<Sidebar selectedRunId={null} onSelectRun={vi.fn()} fetchImpl={fetchImpl} />)

    expect(screen.getByText(/loading runs/i)).toBeInTheDocument()

    expect(await screen.findByText(/titanic/i)).toBeInTheDocument()
    expect(screen.queryByText(/loading runs/i)).not.toBeInTheDocument()
  })

  it('renders a distinct error state when the run list fails to load', async () => {
    const fetchImpl = vi.fn(() =>
      Promise.resolve(jsonResponse({ detail: 'boom' }, 500)),
    ) as unknown as FetchLike

    render(<Sidebar selectedRunId={null} onSelectRun={vi.fn()} fetchImpl={fetchImpl} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load runs/i)
  })

  it('shows "No runs yet." once loaded with an empty list', async () => {
    const fetchImpl = stubFetch({ listRunsResponses: [[]] })

    render(<Sidebar selectedRunId={null} onSelectRun={vi.fn()} fetchImpl={fetchImpl} />)

    expect(await screen.findByText(/no runs yet/i)).toBeInTheDocument()
  })

  it('calls onSelectRun with the run id when a run row is clicked', async () => {
    const runs = [makeRun({ run_id: 'run-7', competition_name: 'house-prices' })]
    const fetchImpl = stubFetch({ listRunsResponses: [runs] })
    const onSelectRun = vi.fn()

    render(<Sidebar selectedRunId={null} onSelectRun={onSelectRun} fetchImpl={fetchImpl} />)

    const runButton = await screen.findByRole('button', { name: /house-prices/i })
    fireEvent.click(runButton)

    expect(onSelectRun).toHaveBeenCalledWith('run-7')
  })

  it('marks the row matching selectedRunId as current', async () => {
    const runs = [makeRun({ run_id: 'run-7', competition_name: 'house-prices' })]
    const fetchImpl = stubFetch({ listRunsResponses: [runs] })

    render(<Sidebar selectedRunId="run-7" onSelectRun={vi.fn()} fetchImpl={fetchImpl} />)

    const runButton = await screen.findByRole('button', { name: /house-prices/i })
    expect(runButton).toHaveAttribute('aria-current', 'true')
  })
})

describe('Sidebar — create run', () => {
  it('submits a snake_case payload, refreshes via listRuns (not the POST body), then selects the new run', async () => {
    const initialRuns: Run[] = []
    const refreshedRuns = [makeRun({ run_id: 'run-new', competition_name: 'new-comp' })]
    const fetchImpl = stubFetch({
      listRunsResponses: [initialRuns, refreshedRuns],
      create: () => jsonResponse({ run_id: 'run-new', status: 'pending' }, 201),
    })
    const onSelectRun = vi.fn()

    render(<Sidebar selectedRunId={null} onSelectRun={onSelectRun} fetchImpl={fetchImpl} />)
    await screen.findByText(/no runs yet/i)

    fireEvent.change(screen.getByLabelText(/competition name/i), {
      target: { value: 'new-comp' },
    })
    fireEvent.change(screen.getByLabelText(/workspace path/i), {
      target: { value: '/workspaces/new-comp' },
    })
    fireEvent.click(screen.getByRole('button', { name: /create run/i }))

    await waitFor(() => expect(onSelectRun).toHaveBeenCalledWith('run-new'))

    const postCall = vi
      .mocked(fetchImpl)
      .mock.calls.find(([, init]) => init?.method === 'POST')
    expect(postCall?.[1]?.body).toBe(
      JSON.stringify({ competition_name: 'new-comp', workspace_path: '/workspaces/new-comp' }),
    )

    // Rendered from the refresh (listRuns), never synthesized from the
    // {run_id, status}-only POST response.
    expect(await screen.findByText(/new-comp/i)).toBeInTheDocument()
  })

  it('shows the backend error detail and does not call onSelectRun when create fails', async () => {
    const fetchImpl = stubFetch({
      listRunsResponses: [[]],
      create: () => jsonResponse({ detail: 'competition_name is required' }, 422),
    })
    const onSelectRun = vi.fn()

    render(<Sidebar selectedRunId={null} onSelectRun={onSelectRun} fetchImpl={fetchImpl} />)
    await screen.findByText(/no runs yet/i)

    fireEvent.change(screen.getByLabelText(/competition name/i), {
      target: { value: 'new-comp' },
    })
    fireEvent.change(screen.getByLabelText(/workspace path/i), {
      target: { value: '/workspaces/new-comp' },
    })
    fireEvent.click(screen.getByRole('button', { name: /create run/i }))

    expect(await screen.findByText(/competition_name is required/i)).toBeInTheDocument()
    expect(onSelectRun).not.toHaveBeenCalled()
  })

  it('does not show a "failed to create" error and clears the form when createRun succeeds but the post-create refresh fails', async () => {
    const fetchImpl = stubFetch({
      // First call (mount) succeeds with an empty list; the second call
      // (post-create refresh) is made to reject via a custom router below.
      listRunsResponses: [[]],
      create: () => jsonResponse({ run_id: 'run-new', status: 'pending' }, 201),
    })
    // Wrap the stub so the *second* GET /api/runs call (the post-create
    // refresh) rejects, while the first (mount) and the POST still resolve.
    let getCallCount = 0
    const flakyFetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url === '/api/runs' && method === 'GET') {
        getCallCount += 1
        if (getCallCount === 2) {
          return Promise.reject(new Error('network blip'))
        }
      }
      return (fetchImpl as unknown as (i: RequestInfo | URL, init?: RequestInit) => Promise<Response>)(
        input,
        init,
      )
    }) as unknown as FetchLike
    const onSelectRun = vi.fn()
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(<Sidebar selectedRunId={null} onSelectRun={onSelectRun} fetchImpl={flakyFetch} />)
    await screen.findByText(/no runs yet/i)

    fireEvent.change(screen.getByLabelText(/competition name/i), {
      target: { value: 'new-comp' },
    })
    fireEvent.change(screen.getByLabelText(/workspace path/i), {
      target: { value: '/workspaces/new-comp' },
    })
    fireEvent.click(screen.getByRole('button', { name: /create run/i }))

    // The run WAS created — selection happens using createRun's own run_id,
    // independent of whether the refresh succeeds.
    await waitFor(() => expect(onSelectRun).toHaveBeenCalledWith('run-new'))

    // No "failed to create" (or any) alert is shown — the run was created.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    // The form is cleared so a resubmit on the same inputs can't create a
    // duplicate run.
    expect(screen.getByLabelText(/competition name/i)).toHaveValue('')
    expect(screen.getByLabelText(/workspace path/i)).toHaveValue('')

    consoleErrorSpy.mockRestore()
  })
})
