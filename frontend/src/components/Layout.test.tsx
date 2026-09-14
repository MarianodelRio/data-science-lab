import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Layout } from './Layout'
import * as client from '../api/client'
import type { ChatConnection } from '../api/client'
import type { ExperimentsResponse, Run } from '../api/types'

// None of Layout's descendants receive an injectable fetchImpl from Layout
// (Sidebar self-fetches; PipelineView/Chat/ActionBar open their own
// SSE/WS/REST connections; the hooks call client.ts directly) — mock the
// client module wholesale, mirroring PipelineView.test.tsx/Chat.test.tsx, so
// none of these mount-time calls ever reach a real network.
vi.mock('../api/client', () => ({
  listRuns: vi.fn(),
  createRun: vi.fn(),
  getRun: vi.fn(),
  subscribeToRunEvents: vi.fn(),
  connectChat: vi.fn(),
  openMlflow: vi.fn(),
  submitRun: vi.fn(),
  getExperiments: vi.fn(),
  getFileContent: vi.fn(),
}))

const listRuns = vi.mocked(client.listRuns)
const createRun = vi.mocked(client.createRun)
const subscribeToRunEvents = vi.mocked(client.subscribeToRunEvents)
const connectChat = vi.mocked(client.connectChat)
const openMlflow = vi.mocked(client.openMlflow)
const getExperiments = vi.mocked(client.getExperiments)
const getFileContent = vi.mocked(client.getFileContent)

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

function fakeChatConnection(): ChatConnection {
  return {
    send: vi.fn(),
    onMessage: vi.fn(),
    onOpen: vi.fn(),
    onError: vi.fn(),
    onClose: vi.fn(),
    close: vi.fn(),
  }
}

beforeEach(() => {
  listRuns.mockReset()
  createRun.mockReset()
  subscribeToRunEvents.mockReset()
  connectChat.mockReset()
  openMlflow.mockReset()
  getExperiments.mockReset()
  getFileContent.mockReset()

  listRuns.mockResolvedValue([])
  subscribeToRunEvents.mockReturnValue(vi.fn())
  connectChat.mockReturnValue(fakeChatConnection())
  openMlflow.mockResolvedValue({ url: 'http://localhost:5000' })
})

describe('Layout', () => {
  it('renders a sidebar landmark wired to fetch the run list on mount', async () => {
    render(<Layout />)
    expect(screen.getByRole('complementary')).toBeInTheDocument()
    await screen.findByText(/no runs yet/i)
    expect(listRuns).toHaveBeenCalledTimes(1)
  })

  it('renders a tablist with the four expected tabs', () => {
    render(<Layout />)
    expect(screen.getByRole('tablist')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /pipeline/i })).toBeInTheDocument()
    expect(
      screen.getByRole('tab', { name: /experiments/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /files/i })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /chat/i })).toBeInTheDocument()
  })

  it('shows the Pipeline panel by default', () => {
    render(<Layout />)
    const panel = screen.getByRole('tabpanel')
    expect(panel).toHaveTextContent(/no run selected/i)
  })

  it('switches the visible tabpanel when another tab is clicked', async () => {
    const user = userEvent.setup()
    render(<Layout />)

    await user.click(screen.getByRole('tab', { name: /experiments/i }))
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /no experiments yet/i,
    )

    await user.click(screen.getByRole('tab', { name: /files/i }))
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /no content available/i,
    )

    await user.click(screen.getByRole('tab', { name: /chat/i }))
    expect(screen.getByRole('tabpanel')).toHaveTextContent(/no run selected/i)
  })

  it('moves focus and selection with ArrowRight/ArrowLeft, wrapping at the ends', async () => {
    const user = userEvent.setup()
    render(<Layout />)

    const pipelineTab = screen.getByRole('tab', { name: /pipeline/i })
    const experimentsTab = screen.getByRole('tab', { name: /experiments/i })
    const chatTab = screen.getByRole('tab', { name: /chat/i })

    pipelineTab.focus()
    await user.keyboard('{ArrowRight}')

    expect(experimentsTab).toHaveFocus()
    expect(experimentsTab).toHaveAttribute('aria-selected', 'true')
    expect(experimentsTab).toHaveAttribute('tabindex', '0')
    expect(pipelineTab).toHaveAttribute('aria-selected', 'false')
    expect(pipelineTab).toHaveAttribute('tabindex', '-1')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      /no experiments yet/i,
    )

    await user.keyboard('{ArrowLeft}')
    expect(pipelineTab).toHaveFocus()
    expect(pipelineTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(/no run selected/i)

    // Wraps backward from the first tab to the last.
    await user.keyboard('{ArrowLeft}')
    expect(chatTab).toHaveFocus()
    expect(chatTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(/no run selected/i)

    // Wraps forward from the last tab back to the first.
    await user.keyboard('{ArrowRight}')
    expect(pipelineTab).toHaveFocus()
    expect(pipelineTab).toHaveAttribute('aria-selected', 'true')
  })

  it('jumps to the first/last tab with Home/End', async () => {
    const user = userEvent.setup()
    render(<Layout />)

    const pipelineTab = screen.getByRole('tab', { name: /pipeline/i })
    const chatTab = screen.getByRole('tab', { name: /chat/i })

    pipelineTab.focus()
    await user.keyboard('{End}')
    expect(chatTab).toHaveFocus()
    expect(chatTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(/no run selected/i)

    await user.keyboard('{Home}')
    expect(pipelineTab).toHaveFocus()
    expect(pipelineTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent(/no run selected/i)
  })

  it('mounts ActionBar unconditionally, with submit disabled until a run is selected', async () => {
    render(<Layout />)

    expect(await screen.findByRole('heading', { name: /actions/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /submit to kaggle/i })).toBeDisabled()
    expect(openMlflow).toHaveBeenCalledTimes(1)
  })
})

describe('Layout — run selection wires all four panels + ActionBar', () => {
  function expermimentsResponse(
    overrides: Partial<ExperimentsResponse> = {},
  ): ExperimentsResponse {
    return {
      experiments: [
        { id: 'exp_0', path: 'experiments/exp_0', cv_score: 0.812, iteration: 0, model: 'lgbm' },
      ],
      baseline_score: 0.75,
      best_experiment_path: 'experiments/exp_0',
      ...overrides,
    }
  }

  async function selectTheOnlyRun(user: ReturnType<typeof userEvent.setup>) {
    render(<Layout />)
    const runButton = await screen.findByRole('button', { name: /titanic/i })
    await user.click(runButton)
  }

  beforeEach(() => {
    listRuns.mockResolvedValue([makeRun({ run_id: 'run-1', competition_name: 'titanic' })])
  })

  it('populates Pipeline, Experiments, Files and Chat, and enables ActionBar, once a run is selected', async () => {
    getExperiments.mockResolvedValue(expermimentsResponse())
    getFileContent.mockResolvedValue('# EDA Report\n\nSome findings.')
    const user = userEvent.setup()

    await selectTheOnlyRun(user)

    // ActionBar: submit becomes enabled once a run is selected.
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /submit to kaggle/i })).not.toBeDisabled(),
    )

    // Pipeline (default tab): no longer the "no run selected" empty state.
    expect(subscribeToRunEvents).toHaveBeenCalledWith('run-1', expect.anything())
    expect(screen.getByRole('tabpanel')).not.toHaveTextContent(/no run selected/i)

    // Experiments.
    await user.click(screen.getByRole('tab', { name: /experiments/i }))
    expect(await screen.findByText('lgbm')).toBeInTheDocument()
    expect(screen.getByText('0.7500')).toBeInTheDocument()

    // Files: the default curated report (EDA Report, markdown) renders its
    // fetched content through FileViewer.
    await user.click(screen.getByRole('tab', { name: /files/i }))
    expect(
      await screen.findByRole('heading', { name: /eda report/i }),
    ).toBeInTheDocument()
    expect(getFileContent).toHaveBeenCalledWith('run-1', 'reports/eda_report.md')

    // Chat: no longer the "no run selected" empty state.
    await user.click(screen.getByRole('tab', { name: /chat/i }))
    expect(connectChat).toHaveBeenCalledWith('run-1')
    expect(screen.getByRole('tabpanel')).not.toHaveTextContent(/no run selected/i)
  })

  it('renders "No baseline yet" (never "0") when baseline_score is null', async () => {
    getExperiments.mockResolvedValue(expermimentsResponse({ baseline_score: null }))
    getFileContent.mockResolvedValue('')
    const user = userEvent.setup()

    await selectTheOnlyRun(user)
    await user.click(screen.getByRole('tab', { name: /experiments/i }))

    await screen.findByText(/no baseline yet/i)
    const baselineRow = screen.getByText('Baseline').closest('tr')
    expect(baselineRow).toHaveTextContent(/no baseline yet/i)
    expect(baselineRow).not.toHaveTextContent('0.0000')
  })

  it('renders the FileViewer empty state (not an error) when a curated report 404s', async () => {
    getExperiments.mockResolvedValue(expermimentsResponse())
    const notFound = new Error('Request failed with status 404') as client.ApiError
    notFound.status = 404
    notFound.detail = 'file is not generated yet'
    getFileContent.mockRejectedValue(notFound)
    const user = userEvent.setup()

    await selectTheOnlyRun(user)
    await user.click(screen.getByRole('tab', { name: /files/i }))

    expect(await screen.findByText(/no content available/i)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders a visible error, distinct from the empty state, when getExperiments fails with a non-404 error', async () => {
    const serverError = new Error('Request failed with status 500') as client.ApiError
    serverError.status = 500
    serverError.detail = 'database unavailable'
    getExperiments.mockRejectedValue(serverError)
    getFileContent.mockResolvedValue('')
    const user = userEvent.setup()

    await selectTheOnlyRun(user)
    await user.click(screen.getByRole('tab', { name: /experiments/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/database unavailable/i)
  })

  it('renders a visible error, distinct from the empty state, when getFileContent fails with a non-404 error', async () => {
    getExperiments.mockResolvedValue(expermimentsResponse())
    const serverError = new Error('Request failed with status 500') as client.ApiError
    serverError.status = 500
    serverError.detail = 'storage backend unreachable'
    getFileContent.mockRejectedValue(serverError)
    const user = userEvent.setup()

    await selectTheOnlyRun(user)
    await user.click(screen.getByRole('tab', { name: /files/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/storage backend unreachable/i)
  })
})
