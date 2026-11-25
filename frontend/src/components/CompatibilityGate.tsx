import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react'
import { useModalAttributes } from '@fluentui/react-components'
import { versionApi } from '../services/api'
import { compatibility } from '../services/compatibility'

export function CompatibilityGate({ children }: { children: ReactNode }) {
  const snapshot = useSyncExternalStore(compatibility.subscribe, compatibility.getSnapshot)
  const [admitted, setAdmitted] = useState(false)
  const notice = useRef<HTMLDialogElement>(null)
  const { modalAttributes } = useModalAttributes({ trapFocus: true })
  const failed = snapshot.status === 'blocked' || snapshot.status === 'unavailable'

  useEffect(() => {
    let active = true
    void compatibility.verify(() => versionApi.getVersion()).then(() => {
      if (active && compatibility.getSnapshot().status === 'ready') setAdmitted(true)
    })
    return () => { active = false }
  }, [])

  useEffect(() => {
    if (failed && notice.current && !notice.current.open) {
      notice.current.showModal()
    }
  }, [failed])

  return (
    <>
      {snapshot.status === 'checking' && <div role="status">Checking frontend and backend compatibility...</div>}
      {failed && (
        <dialog
          {...modalAttributes}
          ref={notice}
          role="alertdialog"
          aria-labelledby="compatibility-notice-title"
          onCancel={(event) => event.preventDefault()}
          style={{ padding: '2rem', maxWidth: 'min(42rem, calc(100vw - 2rem))', overflowWrap: 'anywhere' }}
        >
          <h1 id="compatibility-notice-title">
            {snapshot.status === 'unavailable' ? 'PyRIT backend unavailable' : 'PyRIT compatibility blocked'}
          </h1>
          <p>{snapshot.reason}</p>
          {snapshot.status === 'blocked' && (
            <>
              {snapshot.expected && <p>Backend expected: <code>{snapshot.expected}</code></p>}
              {snapshot.actual && <p>Frontend sent: <code>{snapshot.actual}</code></p>}
              <p>Deploy the frontend and backend from the same version and full Git commit, then reload this page.</p>
            </>
          )}
          {snapshot.status === 'unavailable' && (
            <p>This does not indicate a version mismatch. Wait a few minutes, then reload this page.</p>
          )}
          <p>Further business requests are disabled. No failed operation will be replayed automatically.</p>
          {admitted && <p>Your existing UI state is retained in this page. Reloading will discard unsaved work.</p>}
          <button type="button" onClick={() => window.location.reload()}>Reload page</button>
        </dialog>
      )}
      <div hidden={snapshot.status !== 'ready'}>{admitted && children}</div>
    </>
  )
}
