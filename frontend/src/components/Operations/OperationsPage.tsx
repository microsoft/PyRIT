import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import {
  Badge, Button, MessageBar, MessageBarBody, Spinner, Table, TableBody, TableCell, TableHeader,
  TableHeaderCell, TableRow, Text, Tooltip,
} from '@fluentui/react-components'

import { useFindingFields } from '@/hooks/useFindingFields'
import { operationsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { FindingFields, OperationListItem } from '@/types'
import { severityCountBadges, type SeverityCountBadge } from '@/utils/findingFields'
import { useOperationsStyles } from './Operations.styles'
import OperationCreateDialog from './OperationCreateDialog'

interface FindingCountsProps {
  fields: FindingFields
  counts: Record<string, number>
}

function FindingCounts({ fields, counts }: FindingCountsProps) {
  const styles = useOperationsStyles()
  const badges = severityCountBadges(fields, counts)
  if (badges.length === 0) return <Text size={200} className={styles.muted}>No findings</Text>
  return (
    <div className={styles.badgeGroup}>
      {badges.map((badge: SeverityCountBadge) => {
        const text = `${badge.count} ${badge.label}`
        // The Other badge's key can match an option key, so option badges and it use separate key spaces.
        if (badge.tooltip === null) {
          return <Badge key={`option-${badge.key}`} appearance="tint" color={badge.color}>{text}</Badge>
        }
        return (
          <Tooltip key="unmatched" relationship="description" content={badge.tooltip}>
            <Badge appearance="tint" color={badge.color} tabIndex={0}>{text}</Badge>
          </Tooltip>
        )
      })}
    </div>
  )
}

export default function OperationsPage() {
  const styles = useOperationsStyles()
  const navigate = useNavigate()
  const { fields, error: fieldsError, retry: retryFields } = useFindingFields()
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
      {fieldsError && <MessageBar intent="error"><MessageBarBody>
        Could not load finding fields: {fieldsError}{' '}
        <Button className={styles.button} aria-label="Retry finding fields" onClick={retryFields}>Retry</Button>
      </MessageBarBody></MessageBar>}
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
            {operations.map(operation => (
              <TableRow key={operation.id}>
                <TableCell>
                  <Link to={`/operations/${encodeURIComponent(operation.id)}`} className={styles.link}
                    translate="no">{operation.name}</Link>
                </TableCell>
                <TableCell>
                  {fields !== null ? <FindingCounts fields={fields} counts={operation.finding_counts} />
                    : fieldsError ? <Text size={200} className={styles.muted}>Counts unavailable</Text>
                      : <Spinner size="extra-tiny" aria-label="Loading finding counts" />}
                </TableCell>
                <TableCell>
                  <Text size={200}><time dateTime={operation.created_at}>
                    {new Date(operation.created_at).toLocaleString()}</time></Text>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  )
}
