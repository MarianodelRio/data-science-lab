import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ActionBar } from './ActionBar'
import type { FetchLike } from '../api/client'

const MLFLOW_URL = 'http://localhost:5000'

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response
}

/**
 * Routes a stubbed fetch by exact URL, matching the injected-fetch pattern
 * from client.test.ts. `mlflow` defaults to a resolved MLflow URL so tests
 * focused on the submit flow don't need to care about the mount-time
 * MLflow fetch every ActionBar render triggers.
 */
function stubFetch({
  mlflow = () => Promise.resolve(jsonResponse({ url: MLFLOW_URL })),
  submit,
}: {
  mlflow?: () => Response | Promise<Response>
  submit?: () => Response | Promise<Response>
}): FetchLike {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url === '/api/mlflow/url') return Promise.resolve(mlflow())
    if (submit && url.endsWith('/submit')) return Promise.resolve(submit())
    return Promise.reject(new Error(`unexpected fetch to ${url}`))
  }) as unknown as FetchLike
}

async function clickSubmit() {
  fireEvent.click(screen.getByRole('button', { name: /submit to kaggle/i }))
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ActionBar — submit', () => {
  it('shows a loading state, then the numeric public_score on success', async () => {
    const fetchImpl = stubFetch({
      submit: () =>
        jsonResponse({
          public_score: 0.812,
          submission_file: 'x.csv',
          message: null,
        }),
    })
    render(<ActionBar runId="run-1" fetchImpl={fetchImpl} />)

    await clickSubmit()
    expect(screen.getByText(/submitting/i)).toBeInTheDocument()

    await waitFor(() =>
      expect(screen.getByText(/public score: 0\.812/i)).toBeInTheDocument(),
    )
  })

  it('renders a distinct "not yet scored" state when public_score is null, never "0" or "null"', async () => {
    const fetchImpl = stubFetch({
      submit: () =>
        jsonResponse({
          public_score: null,
          submission_file: 'x.csv',
          message: 'Kaggle accepted the submission but has not scored it yet',
        }),
    })
    render(<ActionBar runId="run-1" fetchImpl={fetchImpl} />)

    await clickSubmit()

    expect(
      await screen.findByText(/accepted — not yet scored/i),
    ).toBeInTheDocument()
    expect(screen.getByText(/has not scored it yet/i)).toBeInTheDocument()
    expect(screen.queryByText('0', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText('null', { exact: true })).not.toBeInTheDocument()
  })

  it('renders "0" (not the "not yet scored" state) when public_score is exactly 0', async () => {
    const fetchImpl = stubFetch({
      submit: () =>
        jsonResponse({
          public_score: 0,
          submission_file: 'x.csv',
          message: null,
        }),
    })
    render(<ActionBar runId="run-1" fetchImpl={fetchImpl} />)

    await clickSubmit()

    expect(await screen.findByText(/public score: 0$/i)).toBeInTheDocument()
    expect(screen.queryByText(/not yet scored/i)).not.toBeInTheDocument()
  })

  it.each([404, 409, 503, 502])(
    'surfaces the backend detail on a %i error response',
    async (status) => {
      const fetchImpl = stubFetch({
        submit: () =>
          jsonResponse({ detail: `submission failed with ${status}` }, status),
      })
      render(<ActionBar runId="run-1" fetchImpl={fetchImpl} />)

      await clickSubmit()

      expect(
        await screen.findByText(
          new RegExp(`submission failed with ${status}`, 'i'),
        ),
      ).toBeInTheDocument()
    },
  )

  it('clears a stale submit result when runId changes to a different run', async () => {
    const fetchImpl = stubFetch({
      submit: () =>
        jsonResponse({ public_score: 0.812, submission_file: 'x.csv', message: null }),
    })
    const { rerender } = render(<ActionBar runId="run-1" fetchImpl={fetchImpl} />)

    await clickSubmit()
    await waitFor(() =>
      expect(screen.getByText(/public score: 0\.812/i)).toBeInTheDocument(),
    )

    rerender(<ActionBar runId="run-2" fetchImpl={fetchImpl} />)

    expect(screen.queryByText(/public score/i)).not.toBeInTheDocument()
  })

  it('clears a stale submit error when runId changes to a different run', async () => {
    const fetchImpl = stubFetch({
      submit: () => jsonResponse({ detail: 'submission failed with 409' }, 409),
    })
    const { rerender } = render(<ActionBar runId="run-1" fetchImpl={fetchImpl} />)

    await clickSubmit()
    expect(
      await screen.findByText(/submission failed with 409/i),
    ).toBeInTheDocument()

    rerender(<ActionBar runId="run-2" fetchImpl={fetchImpl} />)

    expect(screen.queryByText(/submission failed with 409/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('disables the submit button and is a no-op when runId is undefined', async () => {
    const fetchImpl = stubFetch({ submit: () => jsonResponse({}) })
    render(<ActionBar fetchImpl={fetchImpl} />)

    const button = screen.getByRole('button', { name: /submit to kaggle/i })
    expect(button).toBeDisabled()

    fireEvent.click(button)
    await waitFor(() => {
      const submitCalls = vi
        .mocked(fetchImpl)
        .mock.calls.filter(([url]) => String(url).endsWith('/submit'))
      expect(submitCalls).toHaveLength(0)
    })
  })
})

describe('ActionBar — MLflow', () => {
  it('fetches the URL on mount and opens it in a new tab on click', async () => {
    const fetchImpl = stubFetch({})
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(<ActionBar runId="run-1" fetchImpl={fetchImpl} />)

    const button = await screen.findByRole('button', { name: /open mlflow/i })
    await waitFor(() => expect(button).not.toBeDisabled())

    fireEvent.click(button)

    expect(openSpy).toHaveBeenCalledWith(
      MLFLOW_URL,
      '_blank',
      'noopener,noreferrer',
    )
  })

  it('keeps the MLflow button disabled until the mount fetch resolves', async () => {
    let resolveMlflow!: (response: Response) => void
    const pending = new Promise<Response>((resolve) => {
      resolveMlflow = resolve
    })
    const fetchImpl = stubFetch({ mlflow: () => pending })
    render(<ActionBar runId="run-1" fetchImpl={fetchImpl} />)

    expect(screen.getByRole('button', { name: /open mlflow/i })).toBeDisabled()

    resolveMlflow(jsonResponse({ url: MLFLOW_URL }))
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /open mlflow/i }),
      ).not.toBeDisabled(),
    )
  })

  it('does not crash and keeps the button disabled when the mount fetch fails', async () => {
    const consoleErrorSpy = vi
      .spyOn(console, 'error')
      .mockImplementation(() => {})
    const fetchImpl = stubFetch({
      mlflow: () => Promise.reject(new Error('network down')),
    })
    render(<ActionBar runId="run-1" fetchImpl={fetchImpl} />)

    await waitFor(() => expect(consoleErrorSpy).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: /open mlflow/i })).toBeDisabled()
  })
})
