import { useRef, useState, type KeyboardEvent, type ReactElement } from 'react'
import { Sidebar } from './Sidebar'
import { PipelineView } from './PipelineView'
import { ExperimentsTable } from './ExperimentsTable'
import { FileViewer } from './FileViewer'
import { Chat } from './Chat'
import { ActionBar } from './ActionBar'
import { useExperiments } from '../hooks/useExperiments'
import { useFileContent } from '../hooks/useFileContent'

interface TabDef {
  id: string
  label: string
  render: () => ReactElement
}

/**
 * Fixed, curated set of workspace-relative report paths `FileViewer` can
 * show. No backend endpoint lists a run's workspace files (Architect
 * decision, T-050) — `design.md` names exactly these as `FileViewer`'s
 * targets, so the picker offers them directly rather than trying to
 * discover files. Never includes `best_experiment_path`: that path is a
 * directory, and `GET /api/runs/{id}/files/{path}` 404s on directories.
 */
const CURATED_REPORTS = [
  { path: 'reports/eda_report.md', label: 'EDA Report', format: 'markdown' },
  { path: 'reports/final_report.md', label: 'Final Report', format: 'markdown' },
  { path: 'reports/leakage_audit.json', label: 'Leakage Audit', format: 'json' },
] as const

type CuratedReportPath = (typeof CURATED_REPORTS)[number]['path']
type CuratedReportFormat = (typeof CURATED_REPORTS)[number]['format']

/** Parses a curated JSON report's raw text into the value FileViewer expects. */
function parseJsonReport(rawContent: string | null): unknown {
  if (rawContent === null) return undefined
  try {
    return JSON.parse(rawContent)
  } catch (error) {
    console.error('Layout: failed to parse curated JSON report content', error)
    return undefined
  }
}

/** Renders the currently selected curated report through FileViewer,
 * parsing raw text into JSON only when the report's format calls for it. */
function CuratedReportView({
  format,
  rawContent,
}: {
  format: CuratedReportFormat
  rawContent: string | null
}) {
  if (format === 'markdown') {
    return <FileViewer format="markdown" content={rawContent} />
  }
  return <FileViewer format="json" content={parseJsonReport(rawContent)} />
}

function ReportPicker({
  selected,
  onSelect,
}: {
  selected: CuratedReportPath
  onSelect: (path: CuratedReportPath) => void
}) {
  return (
    <div role="tablist" aria-label="Reports">
      {CURATED_REPORTS.map((report) => (
        <button
          key={report.path}
          type="button"
          role="tab"
          aria-selected={report.path === selected}
          onClick={() => onSelect(report.path)}
        >
          {report.label}
        </button>
      ))}
    </div>
  )
}

export function Layout() {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [activeTabId, setActiveTabId] = useState('pipeline')
  const [selectedFilePath, setSelectedFilePath] = useState<CuratedReportPath>(
    CURATED_REPORTS[0].path,
  )

  const {
    experiments,
    baselineScore,
    error: experimentsError,
  } = useExperiments(selectedRunId)
  const { content: fileContent, error: fileContentError } = useFileContent(
    selectedRunId,
    selectedFilePath,
  )

  const currentReport =
    CURATED_REPORTS.find((report) => report.path === selectedFilePath) ?? CURATED_REPORTS[0]

  const TABS: TabDef[] = [
    {
      id: 'pipeline',
      label: 'Pipeline',
      render: () => <PipelineView runId={selectedRunId ?? undefined} />,
    },
    {
      id: 'experiments',
      label: 'Experiments',
      render: () => (
        <div>
          {/* Genuine fetch failures (5xx, network) must stay visually distinct
              from the legitimate "no experiments yet" empty state that
              ExperimentsTable itself renders — see useExperiments.ts. */}
          {experimentsError && <p role="alert">{experimentsError}</p>}
          <ExperimentsTable experiments={experiments} baselineScore={baselineScore} />
        </div>
      ),
    },
    {
      id: 'files',
      label: 'Files',
      render: () => (
        <div>
          <ReportPicker selected={selectedFilePath} onSelect={setSelectedFilePath} />
          {/* A 404 (file not generated yet) is not an error — useFileContent
              already treats that as the empty state and leaves `error` null.
              Only a genuine failure (5xx, network) reaches here. */}
          {fileContentError && <p role="alert">{fileContentError}</p>}
          <CuratedReportView format={currentReport.format} rawContent={fileContent} />
        </div>
      ),
    },
    { id: 'chat', label: 'Chat', render: () => <Chat runId={selectedRunId ?? undefined} /> },
  ]

  const activeTab = TABS.find((tab) => tab.id === activeTabId) ?? TABS[0]
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({})

  const activateTab = (index: number) => {
    const tab = TABS[index]
    setActiveTabId(tab.id)
    tabRefs.current[tab.id]?.focus()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const currentIndex = TABS.findIndex((tab) => tab.id === activeTabId)

    switch (event.key) {
      case 'ArrowRight':
        event.preventDefault()
        activateTab((currentIndex + 1) % TABS.length)
        break
      case 'ArrowLeft':
        event.preventDefault()
        activateTab((currentIndex - 1 + TABS.length) % TABS.length)
        break
      case 'Home':
        event.preventDefault()
        activateTab(0)
        break
      case 'End':
        event.preventDefault()
        activateTab(TABS.length - 1)
        break
      default:
        break
    }
  }

  return (
    <div className="layout">
      <Sidebar selectedRunId={selectedRunId} onSelectRun={setSelectedRunId} />
      <div className="main-column">
        <header>
          {/* Mounted unconditionally — ActionBar fetches the MLflow URL on
              mount (T-042), so a run-conditional mount would refetch it on
              every run switch. Submit stays disabled without a selected run
              via ActionBar's own `runId` gating. */}
          <ActionBar runId={selectedRunId ?? undefined} />
        </header>
        <main>
          <div role="tablist" aria-label="Views" onKeyDown={handleKeyDown}>
            {TABS.map((tab) => {
              const selected = tab.id === activeTabId
              return (
                <button
                  key={tab.id}
                  ref={(el) => {
                    tabRefs.current[tab.id] = el
                  }}
                  type="button"
                  role="tab"
                  id={`tab-${tab.id}`}
                  aria-selected={selected}
                  aria-controls={`tabpanel-${tab.id}`}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => setActiveTabId(tab.id)}
                >
                  {tab.label}
                </button>
              )
            })}
          </div>
          <div
            role="tabpanel"
            id={`tabpanel-${activeTab.id}`}
            aria-labelledby={`tab-${activeTab.id}`}
          >
            {activeTab.render()}
          </div>
        </main>
      </div>
    </div>
  )
}
