import {
  Table,
  TableHeader,
  TableRow,
  TableHeaderCell,
  TableBody,
  TableCell,
  Badge,
  Button,
  Text,
} from '@fluentui/react-components'
import { CheckmarkRegular } from '@fluentui/react-icons'
import type { TargetInstance } from '../../types'
import { useTargetTableStyles } from './TargetTable.styles'

interface TargetTableProps {
  targets: TargetInstance[]
  activeTarget: TargetInstance | null
  onSetActiveTarget: (target: TargetInstance) => void
}

/** Format target_specific_params into a short human-readable string. */
function formatParams(params?: Record<string, unknown> | null): string {
  if (!params) return ''
  const parts: string[] = []
  for (const [key, val] of Object.entries(params)) {
    if (val == null) continue
    if (key === 'extra_body_parameters' && typeof val === 'object') {
      // Flatten nested extra body params for readability
      for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
        parts.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
      }
    } else {
      parts.push(`${key}: ${typeof val === 'object' ? JSON.stringify(val) : String(val)}`)
    }
  }
  return parts.join(', ')
}

export default function TargetTable({ targets, activeTarget, onSetActiveTarget }: TargetTableProps) {
  const styles = useTargetTableStyles()

  const isActive = (target: TargetInstance): boolean =>
    activeTarget?.target_registry_name === target.target_registry_name

  return (
    <div className={styles.tableContainer}>
      <Table aria-label="Target instances" className={styles.table}>
        <TableHeader>
          <TableRow>
            <TableHeaderCell style={{ width: '120px' }} />
            <TableHeaderCell style={{ width: '200px' }}>Type</TableHeaderCell>
            <TableHeaderCell style={{ width: '160px' }}>Model</TableHeaderCell>
            <TableHeaderCell>Endpoint</TableHeaderCell>
            <TableHeaderCell style={{ width: '200px' }}>Parameters</TableHeaderCell>
          </TableRow>
        </TableHeader>
        <TableBody>
          {targets.map((target) => (
            <TableRow
              key={target.target_registry_name}
              className={isActive(target) ? styles.activeRow : undefined}
            >
              <TableCell>
                {isActive(target) ? (
                  <Badge appearance="filled" color="brand" icon={<CheckmarkRegular />}>
                    Active
                  </Badge>
                ) : (
                  <Button
                    appearance="primary"
                    size="small"
                    onClick={() => onSetActiveTarget(target)}
                  >
                    Set Active
                  </Button>
                )}
              </TableCell>
              <TableCell>
                <Badge appearance="outline">{target.target_type}</Badge>
              </TableCell>
              <TableCell>
                <Text size={200}>{target.model_name || '—'}</Text>
              </TableCell>
              <TableCell>
                <Text size={200} className={styles.endpointCell} title={target.endpoint || undefined}>
                  {target.endpoint || '—'}
                </Text>
              </TableCell>
              <TableCell>
                <Text size={200} className={styles.endpointCell} title={formatParams(target.target_specific_params) || undefined}>
                  {formatParams(target.target_specific_params) || '—'}
                </Text>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
