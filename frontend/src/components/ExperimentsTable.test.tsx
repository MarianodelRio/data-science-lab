import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ExperimentsTable } from './ExperimentsTable'
import type { Experiment } from '../api/types'

function makeExperiment(overrides: Partial<Experiment>): Experiment {
  return {
    id: 'exp-1',
    path: '/workspaces/titanic/experiments/exp-1',
    cv_score: 0.8,
    iteration: 1,
    model: 'RandomForest',
    ...overrides,
  }
}

describe('ExperimentsTable — no data', () => {
  it('renders "No experiments yet." when no props are given', () => {
    render(<ExperimentsTable />)

    expect(screen.getByText('No experiments yet.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.queryByRole('row')).toBeNull()
  })

  it('renders "No experiments yet." when experiments is an empty array', () => {
    render(<ExperimentsTable experiments={[]} />)

    expect(screen.getByText('No experiments yet.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).toBeNull()
  })
})

describe('ExperimentsTable — fixture rendering', () => {
  it('renders one row per experiment plus the pinned baseline row', () => {
    const experiments = [
      makeExperiment({ id: 'exp-1', model: 'RandomForest', cv_score: 0.8, iteration: 1 }),
      makeExperiment({ id: 'exp-2', model: 'XGBoost', cv_score: 0.85, iteration: 2 }),
      makeExperiment({ id: 'exp-3', model: 'LightGBM', cv_score: 0.79, iteration: 3 }),
    ]

    render(<ExperimentsTable experiments={experiments} baselineScore={0.75} />)

    // header + baseline + 3 experiments
    expect(screen.getAllByRole('row')).toHaveLength(5)
    expect(screen.getByText('RandomForest')).toBeInTheDocument()
    expect(screen.getByText('XGBoost')).toBeInTheDocument()
    expect(screen.getByText('LightGBM')).toBeInTheDocument()
    expect(screen.getByText('0.8000')).toBeInTheDocument()
    expect(screen.getByText('0.8500')).toBeInTheDocument()
    expect(screen.getByText('0.7900')).toBeInTheDocument()
    expect(screen.getAllByText('1')).toHaveLength(1)
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
  })
})

describe('ExperimentsTable — best experiment marking', () => {
  it('marks only the highest-scoring experiment as Best', () => {
    const experiments = [
      makeExperiment({ id: 'exp-1', model: 'RandomForest', cv_score: 0.8, iteration: 1 }),
      makeExperiment({ id: 'exp-2', model: 'XGBoost', cv_score: 0.9, iteration: 2 }),
      makeExperiment({ id: 'exp-3', model: 'LightGBM', cv_score: 0.7, iteration: 3 }),
    ]

    render(<ExperimentsTable experiments={experiments} baselineScore={0.5} />)

    const rows = screen.getAllByRole('row')
    const bestRow = rows.find((row) => row.textContent?.includes('XGBoost'))
    const otherRows = rows.filter(
      (row) => row.textContent?.includes('RandomForest') || row.textContent?.includes('LightGBM'),
    )

    expect(bestRow).toHaveTextContent('Best')
    otherRows.forEach((row) => expect(row).not.toHaveTextContent('Best'))
  })

  it('breaks a tied top score by the lowest iteration', () => {
    const experiments = [
      makeExperiment({ id: 'exp-1', model: 'RandomForest', cv_score: 0.9, iteration: 3 }),
      makeExperiment({ id: 'exp-2', model: 'XGBoost', cv_score: 0.9, iteration: 1 }),
    ]

    render(<ExperimentsTable experiments={experiments} baselineScore={0.5} />)

    const rows = screen.getAllByRole('row')
    const lowerIterationRow = rows.find((row) => row.textContent?.includes('XGBoost'))
    const higherIterationRow = rows.find((row) => row.textContent?.includes('RandomForest'))

    expect(lowerIterationRow).toHaveTextContent('Best')
    expect(higherIterationRow).not.toHaveTextContent('Best')
  })
})

describe('ExperimentsTable — delta vs baseline', () => {
  it('renders a positive sign for a score above baseline and a negative sign below it', () => {
    const experiments = [
      makeExperiment({ id: 'exp-1', model: 'AboveBaseline', cv_score: 0.85, iteration: 1 }),
      makeExperiment({ id: 'exp-2', model: 'BelowBaseline', cv_score: 0.65, iteration: 2 }),
    ]

    render(<ExperimentsTable experiments={experiments} baselineScore={0.75} />)

    expect(screen.getByText('+0.1000')).toBeInTheDocument()
    expect(screen.getByText('-0.1000')).toBeInTheDocument()
  })

  it('renders an unsigned "0.0000" for an exact tie with the baseline', () => {
    const experiments = [makeExperiment({ id: 'exp-1', model: 'Tied', cv_score: 0.75, iteration: 1 })]

    render(<ExperimentsTable experiments={experiments} baselineScore={0.75} />)

    expect(screen.getByText('0.0000')).toBeInTheDocument()
    expect(screen.queryByText('+0.0000')).toBeNull()
    expect(screen.queryByText('-0.0000')).toBeNull()
  })

  it('renders an unsigned "0.0000" for a sub-precision negative delta instead of "-0.0000"', () => {
    const experiments = [
      makeExperiment({ id: 'exp-1', model: 'NearTie', cv_score: 0.75 - 0.000001, iteration: 1 }),
    ]

    render(<ExperimentsTable experiments={experiments} baselineScore={0.75} />)

    expect(screen.getByText('0.0000')).toBeInTheDocument()
    expect(screen.queryByText('-0.0000')).toBeNull()
    expect(screen.queryByText('+0.0000')).toBeNull()
  })
})

describe('ExperimentsTable — no baseline', () => {
  it('shows "No baseline yet" and a dash delta for every row when baselineScore is null', () => {
    const experiments = [
      makeExperiment({ id: 'exp-1', model: 'RandomForest', cv_score: 0.8, iteration: 1 }),
      makeExperiment({ id: 'exp-2', model: 'XGBoost', cv_score: 0.9, iteration: 2 }),
    ]

    render(<ExperimentsTable experiments={experiments} baselineScore={null} />)

    expect(screen.getByText('No baseline yet')).toBeInTheDocument()
    // baseline row: iteration + delta = 2 dashes; 2 experiment rows: 1 delta dash each
    expect(screen.getAllByText('—')).toHaveLength(4)
  })

  it('shows "No baseline yet" and a dash delta for every row when baselineScore is undefined', () => {
    const experiments = [makeExperiment({ id: 'exp-1', model: 'RandomForest', cv_score: 0.8, iteration: 1 })]

    render(<ExperimentsTable experiments={experiments} />)

    expect(screen.getByText('No baseline yet')).toBeInTheDocument()
  })
})

describe('ExperimentsTable — sorting by score', () => {
  it('reorders rows ascending then descending by cv_score, keeping the baseline pinned first', async () => {
    const user = userEvent.setup()
    const experiments = [
      makeExperiment({ id: 'exp-1', model: 'Mid', cv_score: 0.8, iteration: 1 }),
      makeExperiment({ id: 'exp-2', model: 'High', cv_score: 0.9, iteration: 2 }),
      makeExperiment({ id: 'exp-3', model: 'Low', cv_score: 0.7, iteration: 3 }),
    ]

    render(<ExperimentsTable experiments={experiments} baselineScore={0.5} />)

    await user.click(screen.getByRole('button', { name: 'Score' }))

    // rows[0] is the header row; rows[1] is the pinned baseline row.
    let rows = screen.getAllByRole('row')
    expect(rows[1]).toHaveTextContent('Baseline')
    expect(rows[2]).toHaveTextContent('Low')
    expect(rows[3]).toHaveTextContent('Mid')
    expect(rows[4]).toHaveTextContent('High')

    await user.click(screen.getByRole('button', { name: 'Score' }))

    rows = screen.getAllByRole('row')
    expect(rows[1]).toHaveTextContent('Baseline')
    expect(rows[2]).toHaveTextContent('High')
    expect(rows[3]).toHaveTextContent('Mid')
    expect(rows[4]).toHaveTextContent('Low')
  })
})

describe('ExperimentsTable — sorting by iteration', () => {
  it('reorders rows ascending then descending by iteration, keeping the baseline pinned first', async () => {
    const user = userEvent.setup()
    const experiments = [
      makeExperiment({ id: 'exp-1', model: 'Mid', cv_score: 0.8, iteration: 2 }),
      makeExperiment({ id: 'exp-2', model: 'High', cv_score: 0.9, iteration: 3 }),
      makeExperiment({ id: 'exp-3', model: 'Low', cv_score: 0.7, iteration: 1 }),
    ]

    render(<ExperimentsTable experiments={experiments} baselineScore={0.5} />)

    await user.click(screen.getByRole('button', { name: 'Iteration' }))

    // rows[0] is the header row; rows[1] is the pinned baseline row.
    let rows = screen.getAllByRole('row')
    expect(rows[1]).toHaveTextContent('Baseline')
    expect(rows[2]).toHaveTextContent('Low')
    expect(rows[3]).toHaveTextContent('Mid')
    expect(rows[4]).toHaveTextContent('High')

    await user.click(screen.getByRole('button', { name: 'Iteration' }))

    rows = screen.getAllByRole('row')
    expect(rows[1]).toHaveTextContent('Baseline')
    expect(rows[2]).toHaveTextContent('High')
    expect(rows[3]).toHaveTextContent('Mid')
    expect(rows[4]).toHaveTextContent('Low')
  })
})

describe('ExperimentsTable — aria-sort', () => {
  it('reflects the active sort column and direction on the sortable headers', async () => {
    const user = userEvent.setup()
    const experiments = [
      makeExperiment({ id: 'exp-1', model: 'Mid', cv_score: 0.8, iteration: 1 }),
      makeExperiment({ id: 'exp-2', model: 'High', cv_score: 0.9, iteration: 2 }),
    ]

    render(<ExperimentsTable experiments={experiments} baselineScore={0.5} />)

    const scoreHeader = screen.getByRole('columnheader', { name: 'Score' })
    const iterationHeader = screen.getByRole('columnheader', { name: 'Iteration' })

    expect(scoreHeader).toHaveAttribute('aria-sort', 'none')
    expect(iterationHeader).toHaveAttribute('aria-sort', 'none')

    await user.click(screen.getByRole('button', { name: 'Score' }))

    expect(scoreHeader).toHaveAttribute('aria-sort', 'ascending')
    expect(iterationHeader).toHaveAttribute('aria-sort', 'none')

    await user.click(screen.getByRole('button', { name: 'Score' }))

    expect(scoreHeader).toHaveAttribute('aria-sort', 'descending')
    expect(iterationHeader).toHaveAttribute('aria-sort', 'none')

    await user.click(screen.getByRole('button', { name: 'Iteration' }))

    expect(scoreHeader).toHaveAttribute('aria-sort', 'none')
    expect(iterationHeader).toHaveAttribute('aria-sort', 'ascending')
  })
})
