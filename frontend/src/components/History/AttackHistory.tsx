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

const PAGE_SIZE = 25

type ListParams = Parameters<typeof attacksApi.listAttacks>[0]

function buildListParams(filters: HistoryFilters, pageCursor: string | undefined): ListParams {
  const labelParams: string[] = []
  for (const op of filters.operator) { labelParams.push(`operator:${op}`) }
  for (const op of filters.operation) { labelParams.push(`operation:${op}`) }
  labelParams.push(...filters.otherLabels)

  const params: ListParams = { limit: PAGE_SIZE }
  if (pageCursor) params.cursor = pageCursor
  if (filters.attackTypes.length > 0) params.attack_types = filters.attackTypes
  if (filters.outcome) params.outcome = filters.outcome
  if (filters.converter.length > 0) params.converter_types = filters.converter
  // Match mode is only meaningful with >=2 converters selected.
  if (filters.converter.length >= 2) params.converter_types_match = filters.converterMatchMode
  if (filters.hasConverters !== undefined) params.has_converters = filters.hasConverters
  if (labelParams.length > 0) params.label = labelParams
  return params
}

export default function AttackHistory({ onOpenAttack, filters, onFiltersChange }: AttackHistoryProps) {
  const styles = useAttackHistoryStyles()
  const [attacks, setAttacks] = useState<AttackSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Filter options
  const [attackTypeOptions, setAttackTypeOptions] = useState<string[]>([])
  const [converterOptions, setConverterOptions] = useState<string[]>([])
  const [operatorOptions, setOperatorOptions] = useState<string[]>([])
  const [operationOptions, setOperationOptions] = useState<string[]>([])
  const [otherLabelOptions, setOtherLabelOptions] = useState<string[]>([])

  // Pagination
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [isLastPage, setIsLastPage] = useState(true)
  const [page, setPage] = useState(0)

  // Bumped from event handlers (Refresh button, pagination) to re-trigger the
  // fetch effect without calling setState synchronously inside it.
  const [fetchToken, setFetchToken] = useState({ cursor: undefined as string | undefined, nonce: 0 })

  const fetchAttacks = useCallback((pageCursor?: string) => {
    setLoading(true)
    setError(null)
    setFetchToken(prev => ({ cursor: pageCursor, nonce: prev.nonce + 1 }))
  }, [])

  // Load filter options on mount
  useEffect(() => {
    attacksApi.getAttackOptions()
      .then(resp => setAttackTypeOptions(resp.attack_types))
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

  // Fetch attacks whenever filters change or an event handler bumps fetchToken.
  // All setState calls live in .then/.catch/.finally so we don't trigger
  // react-hooks/set-state-in-effect.
  useEffect(() => {
    let cancelled = false
    attacksApi.listAttacks(buildListParams(filters, fetchToken.cursor))
      .then(response => {
        if (cancelled) return
        setAttacks(response.items.map(attack => ({ ...attack, labels: attack.labels ?? {} })))
        setIsLastPage(!response.pagination.has_more)
        setCursor(response.pagination.next_cursor ?? undefined)
        setError(null)
        // Reset displayed page index when the trigger is a filter change (no
        // explicit cursor). Pagination handlers pass an explicit cursor and
        // update `page` themselves.
        if (!fetchToken.cursor) setPage(0)
      })
      .catch(err => {
        if (cancelled) return
        setAttacks([])
        setError(toApiError(err).detail)
        if (!fetchToken.cursor) setPage(0)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // The filter fields are listed individually rather than as `filters` so the
    // effect only re-runs when a meaningful sub-field changes (the parent
    // creates a new `filters` object on every render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    filters.attackTypes,
    filters.outcome,
    filters.converter,
    filters.converterMatchMode,
    filters.hasConverters,
    filters.operator,
    filters.operation,
    filters.otherLabels,
    fetchToken,
  ])

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
    filters.attackTypes.length > 0 || filters.outcome || filters.converter.length > 0 ||
    filters.hasConverters !== undefined ||
    filters.operator.length > 0 || filters.operation.length > 0 || filters.otherLabels.length > 0

  return (
    <div className={styles.root}>
      <div className={styles.header} data-tour="history-filters">
        <div className={styles.headerRow}>
          <Text as="h1" size={500} weight="semibold">Attack History</Text>
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
          attackTypeOptions={attackTypeOptions}
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
