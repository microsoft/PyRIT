import { useState, useMemo } from 'react'
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
  Tooltip,
  Select,
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
      for (const [k, v] of Object.entries(val as Record<string, unknown>)) {
        parts.push(`${k}: ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
      }
    } else {
      parts.push(`${key}: ${typeof val === 'object' ? JSON.stringify(val) : String(val)}`)
    }
  }
  return parts.join('\n')
}

/** Render the model cell with a tooltip when underlying model differs. */
function ModelCell({ target }: { target: TargetInstance }) {
  const displayName = target.model_name || '—'
  const hasUnderlying = target.underlying_model_name
    && target.model_name
    && target.underlying_model_name !== target.model_name

  if (hasUnderlying) {
    return (
      <Tooltip
        content={`Underlying model: ${target.underlying_model_name}`}
        relationship="description"
      >
        <Text size={200} style={{ textDecoration: 'underline dotted', cursor: 'help' }}>
          {displayName}
        </Text>
      </Tooltip>
    )
  }

  return <Text size={200}>{displayName}</Text>
}

export default function TargetTable({ targets, activeTarget, onSetActiveTarget }: TargetTableProps) {
  const styles = useTargetTableStyles()
  const [typeFilter, setTypeFilter] = useState('')

  const targetTypes = useMemo(
    () => Array.from(new Set(targets.map(t => t.target_type))).sort(),
    [targets],
  )

  const filteredTargets = useMemo(
    () => typeFilter ? targets.filter(t => t.target_type === typeFilter) : targets,
    [targets, typeFilter],
  )

  const isActive = (target: TargetInstance): boolean =>
    activeTarget?.target_registry_name === target.target_registry_name

  return (
    <div className={styles.tableContainer}>
      {activeTarget && (
        <Table aria-label="Active target" className={styles.table} style={{ marginBottom: '12px' }}>
          <TableBody>
            <TableRow className={styles.activeRow}>
              <TableCell style={{ width: '120px' }}>
                <Badge appearance="filled" color="brand" icon={<CheckmarkRegular />}>Active</Badge>
              </TableCell>
              <TableCell style={{ width: '200px' }}>
                <Badge appearance="outline">{activeTarget.target_type}</Badge>
              </TableCell>
              <TableCell style={{ width: '180px' }}>
                <ModelCell target={activeTarget} />
              </TableCell>
              <TableCell style={{ minWidth: '300px' }}>
                <Text size={200} className={styles.endpointCell} title={activeTarget.endpoint || undefined}>
                  {activeTarget.endpoint || '—'}
                </Text>
              </TableCell>
              <TableCell style={{ width: '200px' }}>
                <Text size={200} className={styles.paramsCell}>
                  {formatParams(activeTarget.target_specific_params) || '—'}
                </Text>
              </TableCell>
            </TableRow>
          </TableBody>
        </Table>
      )}

      {targetTypes.length > 1 && (
        <div style={{ marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Text size={200}>Filter by type:</Text>
          <Select
            value={typeFilter}
            onChange={(_, data) => setTypeFilter(data.value)}
            style={{ minWidth: '200px' }}
          >
            <option value="">All types</option>
            {targetTypes.map(t => (
              <option key={t} value={t}>{t}</option>
            ))}
          </Select>
        </div>
      )}

      <Table aria-label="Target instances" className={styles.table}>
        <TableHeader>
          <TableRow>
            <TableHeaderCell style={{ width: '120px' }} />
            <TableHeaderCell style={{ width: '200px' }}>Type</TableHeaderCell>
            <TableHeaderCell style={{ width: '180px' }}>Model</TableHeaderCell>
            <TableHeaderCell style={{ minWidth: '300px' }}>Endpoint</TableHeaderCell>
            <TableHeaderCell style={{ width: '200px' }}>Parameters</TableHeaderCell>
          </TableRow>
        </TableHeader>
        <TableBody>
          {filteredTargets.map((target) => (
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
                <ModelCell target={target} />
              </TableCell>
              <TableCell>
                <Text size={200} className={styles.endpointCell} title={target.endpoint || undefined}>
                  {target.endpoint || '—'}
                </Text>
              </TableCell>
              <TableCell>
                <Text size={200} className={styles.paramsCell}>
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
