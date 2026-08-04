import { useState, type ReactElement } from 'react'
import { Sidebar } from './Sidebar'
import { PipelineView } from './PipelineView'
import { ExperimentsTable } from './ExperimentsTable'
import { FileViewer } from './FileViewer'
import { Chat } from './Chat'

interface TabDef {
  id: string
  label: string
  render: () => ReactElement
}

const TABS: TabDef[] = [
  { id: 'pipeline', label: 'Pipeline', render: () => <PipelineView /> },
  {
    id: 'experiments',
    label: 'Experiments',
    render: () => <ExperimentsTable />,
  },
  { id: 'files', label: 'Files', render: () => <FileViewer /> },
  { id: 'chat', label: 'Chat', render: () => <Chat /> },
]

export function Layout() {
  const [activeTabId, setActiveTabId] = useState(TABS[0].id)
  const activeTab = TABS.find((tab) => tab.id === activeTabId) ?? TABS[0]

  return (
    <div className="layout">
      <Sidebar />
      <main>
        <div role="tablist" aria-label="Views">
          {TABS.map((tab) => {
            const selected = tab.id === activeTabId
            return (
              <button
                key={tab.id}
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
  )
}
