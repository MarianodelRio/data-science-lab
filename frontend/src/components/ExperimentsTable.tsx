import { useMemo, useState } from 'react'
import type { Experiment } from '../api/types'

type SortColumn = 'score' | 'iteration'
type SortDirection = 'asc' | 'desc'

interface ExperimentsTableProps {
  experiments?: Experiment[]
  baselineScore?: number | null
}

/**
 * Finds the best experiment by highest `cv_score` (ties broken by lowest
 * `iteration`), per the system-wide higher-is-better convention encoded in
 * `LabState.best_score`'s `-inf` seed (src/state.py). Never affected by sort
 * order or the baseline — pure over `experiments` only.
 */
function findBestId(experiments: Experiment[]): string | null {
  if (experiments.length === 0) return null
  return experiments.reduce((best, current) => {
    if (current.cv_score > best.cv_score) return current
    if (current.cv_score === best.cv_score && current.iteration < best.iteration) {
      return current
    }
    return best
  }).id
}

/**
 * `null`/`undefined` baseline means "no baseline has run yet" (see
 * src/state.py's `baseline_score=0.0` seed — indistinguishable from a
 * genuine zero score) — the delta is deliberately left uncomputed rather
 * than assuming 0.
 */
function computeDelta(cvScore: number, baselineScore: number | null | undefined): number | null {
  if (baselineScore === null || baselineScore === undefined) return null
  return cvScore - baselineScore
}

function formatDelta(delta: number | null): string {
  if (delta === null) return '—'
  const sign = delta >= 0 ? '+' : ''
  return `${sign}${delta.toFixed(4)}`
}

export function ExperimentsTable({ experiments, baselineScore }: ExperimentsTableProps) {
  const [sortColumn, setSortColumn] = useState<SortColumn | null>(null)
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc')

  const bestId = useMemo(
    () => findBestId(experiments ?? []),
    [experiments],
  )

  const sortedExperiments = useMemo(() => {
    if (!experiments || sortColumn === null) return experiments ?? []
    const factor = sortDirection === 'asc' ? 1 : -1
    const field = sortColumn === 'score' ? 'cv_score' : 'iteration'
    return [...experiments].sort((a, b) => (a[field] - b[field]) * factor)
  }, [experiments, sortColumn, sortDirection])

  if (!experiments || experiments.length === 0) {
    return <p>No experiments yet.</p>
  }

  function handleSort(column: SortColumn) {
    if (column === sortColumn) {
      setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortColumn(column)
      setSortDirection('asc')
    }
  }

  const hasBaseline = baselineScore !== null && baselineScore !== undefined

  return (
    <div>
      <h2>Experiments</h2>
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>
              <button type="button" onClick={() => handleSort('score')}>
                Score
              </button>
            </th>
            <th>
              <button type="button" onClick={() => handleSort('iteration')}>
                Iteration
              </button>
            </th>
            <th>Delta vs Baseline</th>
            <th>Best</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Baseline</td>
            <td>{hasBaseline ? baselineScore.toFixed(4) : 'No baseline yet'}</td>
            <td>—</td>
            <td>—</td>
            <td></td>
          </tr>
          {sortedExperiments.map((experiment) => (
            <tr key={experiment.id}>
              <td>{experiment.model}</td>
              <td>{experiment.cv_score.toFixed(4)}</td>
              <td>{experiment.iteration}</td>
              <td>{formatDelta(computeDelta(experiment.cv_score, baselineScore))}</td>
              <td>{experiment.id === bestId ? 'Best' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
