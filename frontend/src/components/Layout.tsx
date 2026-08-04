import { useRef, useState, type KeyboardEvent, type ReactElement } from 'react'
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
      <Sidebar />
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
  )
}
