/**
 * Static placeholder for the runs sidebar. Not wired to listRuns() — no
 * functionality yet. design.md's frontend component table does not list a
 * dedicated Sidebar/RunList component and no task currently owns wiring it
 * to the API; this keeps T-038 scoped to a non-functional layout shell.
 */
export function Sidebar() {
  return (
    <aside aria-label="Runs">
      <h2>Runs</h2>
      <p>No runs yet.</p>
    </aside>
  )
}
