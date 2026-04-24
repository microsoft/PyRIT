import { useState, useEffect, useCallback } from 'react'
import {
  Text,
  Button,
  Spinner,
  MessageBar,
  MessageBarBody,
} from '@fluentui/react-components'
import { ArrowSyncRegular } from '@fluentui/react-icons'
import { attacksApi, labelsApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type { AttackSummary } from '../../types'
import type { HistoryFilters } from './historyFilters'
import { useAttackHistoryStyles } from './AttackHistory.styles'
import HistoryFiltersBar from './HistoryFiltersBar'
import AttackTable from './AttackTable'
import HistoryPagination from './HistoryPagination'

interface AttackHistoryProps {
  onOpenAttack: (attackResultId: string) => void
  filters: HistoryFilters
  onFiltersChange: (filters: HistoryFilters) => void
}

export default function AttackHistory({ onOpenAttack, filters, onFiltersChange }: AttackHistoryProps) {
  const styles = useAttackHistoryStyles()
  const [attacks, setAttacks] = useState<AttackSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Filter options
  const [attackClassOptions, setAttackClassOptions] = useState<string[]>([])
  const [converterOptions, setConverterOptions] = useState<string[]>([])
  const [operatorOptions, setOperatorOptions] = useState<string[]>([])
  const [operationOptions, setOperationOptions] = useState<string[]>([])
  const [otherLabelOptions, setOtherLabelOptions] = useState<string[]>([])

  // Pagination
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [isLastPage, setIsLastPage] = useState(true)
  const [page, setPage] = useState(0)

  const PAGE_SIZE = 25

  const fetchAttacks = useCallback(async (pageCursor?: string) => {
    setLoading(true)
    setError(null)
    try {
      const labelParams: string[] = []
      if (filters.operator) { labelParams.push(`operator:${filters.operator}`) }
      if (filters.operation) { labelParams.push(`operation:${filters.operation}`) }
      labelParams.push(...filters.otherLabels)

      const response = await attacksApi.listAttacks({
        limit: PAGE_SIZE,
        ...(pageCursor && { cursor: pageCursor }),
        ...(filters.attackClass && { attack_type: filters.attackClass }),
        ...(filters.outcome && { outcome: filters.outcome }),
        ...(filters.converter && { converter_types: [filters.converter] }),
        ...(labelParams.length > 0 && { label: labelParams }),
      })
      setAttacks(response.items.map(attack => ({ ...attack, labels: attack.labels ?? {} })))
      setIsLastPage(!response.pagination.has_more)
      setCursor(response.pagination.next_cursor ?? undefined)
    } catch (err) {
      setAttacks([])
      setError(toApiError(err).detail)
    } finally {
      setLoading(false)
    }
  }, [filters.attackClass, filters.outcome, filters.converter, filters.operator, filters.operation, filters.otherLabels])

  // Load filter options on mount
  useEffect(() => {
    attacksApi.getAttackOptions()
      .then(resp => setAttackClassOptions(resp.attack_types))
      .catch(() => { /* ignore */ })
    attacksApi.getConverterOptions()
      .then(resp => setConverterOptions(resp.converter_types))
      .catch(() => { /* ignore */ })
    labelsApi.getLabels()
      .then(resp => {
        const operators: string[] = []
        const operations: string[] = []
        const others: string[] = []
        for (const [key, values] of Object.entries(resp.labels)) {
          if (key === 'operator') {
            operators.push(...values)
          } else if (key === 'operation') {
            operations.push(...values)
          } else if (key !== 'source') {
            for (const val of values) {
              others.push(`${key}:${val}`)
            }
          }
        }
        setOperatorOptions(operators.sort())
        setOperationOptions(operations.sort())
        setOtherLabelOptions(others.sort())
      })
      .catch(() => { /* ignore */ })
  }, [])

  // Reload when filters change
  useEffect(() => {
    setPage(0)
    setCursor(undefined)
    fetchAttacks()
  }, [fetchAttacks])

  const handleNextPage = () => {
    if (cursor) {
      setPage(p => p + 1)
      fetchAttacks(cursor)
    }
  }

  const handlePrevPage = () => {
    setPage(0)
    setCursor(undefined)
    fetchAttacks()
  }

  const formatDate = (dateStr: string) => {
    const date = new Date(dateStr)
    return date.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const hasActiveFilters =
    filters.attackClass || filters.outcome || filters.converter ||
    filters.operator || filters.operation || filters.otherLabels.length > 0

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <div className={styles.headerRow}>
          <Text size={500} weight="semibold">Attack History</Text>
          <Button
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            onClick={() => fetchAttacks()}
            disabled={loading}
            data-testid="refresh-btn"
          >
            Refresh
          </Button>
        </div>
        <HistoryFiltersBar
          filters={filters}
          onFiltersChange={onFiltersChange}
          attackClassOptions={attackClassOptions}
          converterOptions={converterOptions}
          operatorOptions={operatorOptions}
          operationOptions={operationOptions}
          otherLabelOptions={otherLabelOptions}
        />
      </div>

      <div className={styles.content}>
        {loading ? (
          <div className={styles.emptyState}>
            <Spinner size="medium" label="Loading attacks..." />
          </div>
        ) : error ? (
          <div className={styles.emptyState} data-testid="error-state">
            <MessageBar intent="error">
              <MessageBarBody>{error}</MessageBarBody>
            </MessageBar>
            <Button
              appearance="primary"
              icon={<ArrowSyncRegular />}
              onClick={() => fetchAttacks()}
              disabled={loading}
              data-testid="retry-btn"
            >
              Retry
            </Button>
          </div>
        ) : attacks.length === 0 ? (
          <div className={styles.emptyState} data-testid="empty-state">
            <Text size={400}>No attacks found</Text>
            <Text size={200}>
              {hasActiveFilters
                ? 'Try adjusting your filters.'
                : 'Run an attack to see it here.'}
            </Text>
          </div>
        ) : (
          <AttackTable attacks={attacks} onOpenAttack={onOpenAttack} formatDate={formatDate} />
        )}
      </div>

      {!loading && attacks.length > 0 && (
        <HistoryPagination
          page={page}
          isLastPage={isLastPage}
          onPrevPage={handlePrevPage}
          onNextPage={handleNextPage}
        />
      )}
    </div>
  )
}
