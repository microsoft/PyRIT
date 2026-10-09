import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import {
  Button, MessageBar, MessageBarBody, Spinner, Text,
} from '@fluentui/react-components'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { Operation } from '@/types'
import { useOperationsStyles } from './Operations.styles'
import OperationCreateDialog from './OperationCreateDialog'

export default function OperationsPage() {
  const styles = useOperationsStyles()
  const navigate = useNavigate()
  const [operations, setOperations] = useState<Operation[]>([])
  const [settledRevision, setSettledRevision] = useState(-1)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const loading = settledRevision !== revision

  useEffect(() => {
    let ignore = false
    operationsApi.list()
      .then(result => { if (!ignore) { setOperations(result.items); setError(''); setSettledRevision(revision) } })
      .catch((cause: unknown) => { if (!ignore) { setError(toApiError(cause).detail); setSettledRevision(revision) } })
    return () => { ignore = true }
  }, [revision])

  return (
    <section className={styles.root} aria-label="Operations">
      <header className={styles.header}>
        <Text as="h1" size={600} weight="semibold" className={styles.heading}>Operations</Text>
        <OperationCreateDialog
          trigger={<Button appearance="primary" className={styles.button}>New operation</Button>}
          onCreated={operation => { void navigate(`/operations/${encodeURIComponent(operation.id)}`) }}
        />
      </header>
      <Text>Engagements that group human findings.</Text>
      {loading ? <Spinner label="Loading operations…" /> : error ? (
        <MessageBar intent="error"><MessageBarBody>
          Could not load operations: {error}{' '}
          <Button className={styles.button} onClick={() => { setRevision(value => value + 1) }}>Retry</Button>
        </MessageBarBody></MessageBar>
      ) : operations.length === 0 ? (
        <Text>No operations yet. Create one to start recording findings.</Text>
      ) : (
        <ul className={styles.list}>
          {operations.map(operation => (
            <li key={operation.id} className={styles.item}>
              <Link to={`/operations/${encodeURIComponent(operation.id)}`} className={styles.link}
                translate="no">{operation.name}</Link>
              <div className={styles.metadata}>
                <Text size={200}>Created <time dateTime={operation.created_at}>
                  {new Date(operation.created_at).toLocaleString()}</time></Text>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
