import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react'
import { versionApi } from '../services/api'
import { compatibility } from '../services/compatibility'

export function CompatibilityGate({ children }: { children: ReactNode }) {
  const snapshot = useSyncExternalStore(compatibility.subscribe, compatibility.getSnapshot)
  const [admitted, setAdmitted] = useState(false)

  useEffect(() => {
    void compatibility.verify(() => versionApi.getVersion())
  }, [])

  if (snapshot.status === 'ready' && !admitted) setAdmitted(true)

  return (
    <>
      {snapshot.status === 'checking' && <div role="status">Checking frontend and backend compatibility...</div>}
      {snapshot.status === 'blocked' && (
        <section role="alert" style={{ padding: '2rem' }}>
          <h1>PyRIT compatibility blocked</h1>
          <p>{snapshot.reason}</p>
          {snapshot.expected && <p>Backend expected: <code>{snapshot.expected}</code></p>}
          {snapshot.actual && <p>Frontend sent: <code>{snapshot.actual}</code></p>}
          <p>Deploy the frontend and backend from the same version and full Git commit, then reload this page.</p>
          <p>Further business requests are disabled. No failed operation will be replayed automatically.</p>
          {admitted && <p>Your existing UI state is retained in this page. Reloading will discard unsaved work.</p>}
          <button type="button" onClick={() => window.location.reload()}>Reload page</button>
        </section>
      )}
      <div hidden={snapshot.status !== 'ready'}>{admitted && children}</div>
    </>
  )
}
