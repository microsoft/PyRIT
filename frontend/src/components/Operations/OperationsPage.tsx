import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import {
  Badge, Button, MessageBar, MessageBarBody, Spinner, Table, TableBody, TableCell, TableHeader,
  TableHeaderCell, TableRow, Text,
} from '@fluentui/react-components'

import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { FindingSeverity, OperationListItem } from '@/types'
import { FINDING_SEVERITY_LABELS, findingSeverityColor } from '@/utils/findingSeverity'
import { useOperationsStyles } from './Operations.styles'
import OperationCreateDialog from './OperationCreateDialog'

const SEVERITY_ORDER = Object.keys(FINDING_SEVERITY_LABELS) as FindingSeverity[]

export default function OperationsPage() {
  const styles = useOperationsStyles()
  const navigate = useNavigate()
  const [operations, setOperations] = useState<OperationListItem[]>([])
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
        <Table className={styles.table} aria-label="Operations list">
          <TableHeader>
            <TableRow>
              <TableHeaderCell className={styles.colName}>Name</TableHeaderCell>
              <TableHeaderCell className={styles.colFindings}>Findings</TableHeaderCell>
              <TableHeaderCell className={styles.colDate}>Created</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {operations.map(operation => {
              const counts = SEVERITY_ORDER.flatMap(severity => {
                const count = operation.finding_counts[severity]
                return count ? [{ severity, count }] : []
              })
              return (
                <TableRow key={operation.id}>
                  <TableCell>
                    <Link to={`/operations/${encodeURIComponent(operation.id)}`} className={styles.link}
                      translate="no">{operation.name}</Link>
                  </TableCell>
                  <TableCell>
                    {counts.length > 0 ? (
                      <div className={styles.badgeGroup}>
                        {counts.map(({ severity, count }) => (
                          <Badge key={severity} appearance="tint" color={findingSeverityColor(severity)}>
                            {count} {FINDING_SEVERITY_LABELS[severity]}
                          </Badge>
                        ))}
                      </div>
                    ) : <Text size={200} className={styles.muted}>No findings</Text>}
                  </TableCell>
                  <TableCell>
                    <Text size={200}><time dateTime={operation.created_at}>
                      {new Date(operation.created_at).toLocaleString()}</time></Text>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </section>
  )
}
