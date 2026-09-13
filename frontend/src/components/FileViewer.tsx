import ReactMarkdown from 'react-markdown'

/**
 * Purely presentational — no fetch, no `client.ts` call. No backend endpoint
 * serves workspace file content today; the caller is responsible for
 * sourcing `content` (e.g. eda_report.md / final_report.md text for
 * `'markdown'`, feature_importance / results JSON for `'json'`) and passing
 * it in as a prop. Format is chosen via the `format` prop, never
 * auto-detected from `content`.
 */
type FileViewerProps =
  | { format: 'markdown'; content?: string | null }
  | { format: 'json'; content?: unknown }

/** Renders a raw markdown string as formatted HTML with no raw-HTML support
 * (default `react-markdown` configuration only — no `rehype-raw`), so this
 * never risks the `dangerouslySetInnerHTML` XSS surface. */
function MarkdownContent({ content }: { content: string }) {
  return <ReactMarkdown>{content}</ReactMarkdown>
}

/** `value === null || value === undefined` render as distinct literals so a
 * JSON `null` is never confused with an actually-missing field. */
function JsonNode({ value }: { value: unknown }) {
  if (value === null) return <>null</>
  if (value === undefined) return <>—</>

  if (Array.isArray(value)) {
    return (
      <ul>
        {value.map((item, index) => (
          <li key={index}>
            <JsonNode value={item} />
          </li>
        ))}
      </ul>
    )
  }

  if (typeof value === 'object') {
    return (
      <dl>
        {Object.entries(value).map(([key, entryValue]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>
              <JsonNode value={entryValue} />
            </dd>
          </div>
        ))}
      </dl>
    )
  }

  return <>{String(value)}</>
}

function isBlankMarkdown(content: string | null | undefined): boolean {
  return !content || content.trim().length === 0
}

export function FileViewer(props: FileViewerProps) {
  if (props.format === 'markdown') {
    if (isBlankMarkdown(props.content)) {
      return <p>No content available.</p>
    }
    return <MarkdownContent content={props.content as string} />
  }

  if (props.content === undefined || props.content === null) {
    return <p>No content available.</p>
  }
  return <JsonNode value={props.content} />
}
