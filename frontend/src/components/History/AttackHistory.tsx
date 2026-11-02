import { useState, useEffect, useCallback } from 'react'
import {
  Text,
  Button,
  Spinner,
  MessageBar,
  MessageBarBody,
} from '@fluentui/react-components'
import { ArrowSyncRegular, ChatRegular, SettingsRegular } from '@fluentui/react-icons'
import { attacksApi, labelsApi, operationsApi } from '../../services/api'
import { toApiError } from '../../services/errors'
import type { AttackSummary, FilterOption, TargetInstance } from '../../types'
import type { ViewName } from '../Sidebar/Navigation'
import type { HistoryFilters } from './historyFilters'
import { useAttackHistoryStyles } from './AttackHistory.styles'
import HistoryFiltersBar from './HistoryFiltersBar'
import AttackTable from './AttackTable'
import HistoryPagination from './HistoryPagination'

interface AttackHistoryProps {
  onOpenAttack: (attackResultId: string) => void
  filters: HistoryFilters
  onFiltersChange: (filters: HistoryFilters) => void
  activeTarget: TargetInstance | null
  onNavigate: (view: ViewName) => void
  showTitle?: boolean
}

const PAGE_SIZE = 25

type ListParams = Parameters<typeof attacksApi.listAttacks>[0]

/**
 * One history page request. The cursor and the displayed page index always
 * change together, and `filterKey` records which filter visit they belong to.
 */
interface PageRequest {
  filterKey: string
  cursor: string | undefined
  page: number
  requestId: number
}

function buildListParams(filters: HistoryFilters, pageCursor: string | undefined): ListParams {
  const labelParams = [...filters.otherLabels]

  const params: ListParams = { limit: PAGE_SIZE }
  if (pageCursor) params.cursor = pageCursor
  if (filters.attackTypes.length > 0) params.attack_types = filters.attackTypes
  if (filters.outcome) params.outcome = filters.outcome
  if (filters.operator.length > 0) params.operator = filters.operator
  if (filters.operationId.length > 0) params.operation_id = filters.operationId
  if (filters.converter.length > 0) params.converter_types = filters.converter
  // Match mode is only meaningful with >=2 converters selected.
  if (filters.converter.length >= 2) params.converter_types_match = filters.converterMatchMode
  if (filters.hasConverters !== undefined) params.has_converters = filters.hasConverters
  params.include_scenario_attacks = filters.includeScenarioAttacks
  if (labelParams.length > 0) params.label = labelParams
  return params
}

function buildOtherLabelOptions(labels: Record<string, string[]>): string[] {
  const options: string[] = []
  for (const [key, values] of Object.entries(labels)) {
    if (key === 'operator' || key === 'operation') continue
    for (const value of values) {
      options.push(`${key}:${value}`)
    }
  }
  return options.sort()
}

export default function AttackHistory({
  onOpenAttack,
  filters,
  onFiltersChange,
  activeTarget,
  onNavigate,
  showTitle = true,
}: AttackHistoryProps) {
  const styles = useAttackHistoryStyles()
  const [attacks, setAttacks] = useState<AttackSummary[]>([])
  const [error, setError] = useState<string | null>(null)

  // Filter options
  const [attackTypeOptions, setAttackTypeOptions] = useState<string[]>([])
  const [converterOptions, setConverterOptions] = useState<string[]>([])
  const [operatorOptions, setOperatorOptions] = useState<string[]>([])
  const [operationOptions, setOperationOptions] = useState<FilterOption[]>([])
  const [allOtherLabelOptions, setAllOtherLabelOptions] = useState<string[]>([])
  const [narrowedOtherLabelOptions, setNarrowedOtherLabelOptions] = useState<{
    filterKey: string
    options: string[]
  } | null>(null)

  // Pagination
  const [nextCursor, setNextCursor] = useState<string | undefined>(undefined)
  const [isLastPage, setIsLastPage] = useState(true)
  const filterKey = JSON.stringify([
    filters.attackTypes,
    filters.outcome,
    filters.converter,
    filters.converterMatchMode,
    filters.hasConverters,
    filters.includeScenarioAttacks,
    filters.operator,
    filters.operationId,
    filters.otherLabels,
  ])
  const [pageRequest, setPageRequest] = useState<PageRequest>(() => ({
    filterKey,
    cursor: undefined,
    page: 0,
    requestId: 0,
  }))
  const [settledRequestId, setSettledRequestId] = useState<number | null>(null)
  // Every filter change (including A -> B -> A, even while B is still loading)
  // starts a new visit on the first page, so a cursor from an earlier visit of
  // the same filters is never reused.
  // Uses the "adjust state during render" pattern (see React docs:
  // https://react.dev/reference/react/useState#storing-information-from-previous-renders)
  // instead of a useEffect so we don't trigger react-hooks/set-state-in-effect.
  if (pageRequest.filterKey !== filterKey) {
    setPageRequest({ filterKey, cursor: undefined, page: 0, requestId: pageRequest.requestId + 1 })
  }
  const labelOptionFilterKey = JSON.stringify([
    filters.operator,
    filters.otherLabels,
  ])
  const hasLabelOptionFilters = filters.operator.length > 0
    || filters.otherLabels.length > 0
  const otherLabelOptions = hasLabelOptionFilters
    && narrowedOtherLabelOptions?.filterKey === labelOptionFilterKey
    ? narrowedOtherLabelOptions.options
    : allOtherLabelOptions

  // Called from event handlers (Refresh button, pagination) to re-trigger the
  // fetch effect without calling setState synchronously inside it.
  const fetchAttacks = useCallback((pageCursor: string | undefined, page: number) => {
    setError(null)
    setPageRequest(prev => ({ ...prev, cursor: pageCursor, page, requestId: prev.requestId + 1 }))
  }, [])

  // Attack and converter options do not depend on the active history filters.
  useEffect(() => {
    attacksApi.getAttackOptions()
      .then(resp => setAttackTypeOptions(resp.attack_types))
      .catch(() => { /* ignore */ })
    attacksApi.getConverterOptions()
      .then(resp => setConverterOptions(resp.converter_types))
      .catch(() => { /* ignore */ })
    labelsApi.getLabels()
      .then(resp => {
        // TODO(PyRIT 1.4): Remove the labels.* fallbacks with legacy attribution aliases.
        setOperatorOptions([...(resp.operators ?? resp.labels.operator ?? [])].sort())
        setAllOtherLabelOptions(buildOtherLabelOptions(resp.labels))
      })
      .catch(() => { /* ignore */ })
    operationsApi.list()
      .then(resp => setOperationOptions(resp.items.map(operation => ({ value: operation.id, label: operation.name }))))
      .catch(() => { /* ignore */ })
  }, [])

  // Arbitrary label options are narrowed by the active indexed attribution filters.
  useEffect(() => {
    if (!hasLabelOptionFilters) return
    let cancelled = false
    labelsApi.getLabels('attacks', {
      operator: filters.operator.length > 0 ? filters.operator : undefined,
      label: filters.otherLabels.length > 0 ? filters.otherLabels : undefined,
    })
      .then(resp => {
        if (cancelled) return
        setNarrowedOtherLabelOptions({
          filterKey: labelOptionFilterKey,
          options: buildOtherLabelOptions(resp.labels),
        })
      })
      .catch(() => { /* ignore */ })
    return () => {
      cancelled = true
    }
  }, [
    filters.operator,
    filters.otherLabels,
    hasLabelOptionFilters,
    labelOptionFilterKey,
  ])

  // Fetch attacks whenever filters change or an event handler issues a new
  // page request. All setState calls live in .then/.catch so we don't trigger
  // react-hooks/set-state-in-effect.
  useEffect(() => {
    let cancelled = false
    const { cursor, requestId } = pageRequest
    attacksApi.listAttacks(buildListParams(filters, cursor))
      .then(response => {
        if (cancelled) return
        setAttacks(response.items.map(attack => ({ ...attack, labels: attack.labels ?? {} })))
        setIsLastPage(!response.pagination.has_more)
        setNextCursor(response.pagination.next_cursor ?? undefined)
        setSettledRequestId(requestId)
        setError(null)
      })
      .catch(err => {
        if (cancelled) return
        setAttacks([])
        setSettledRequestId(requestId)
        setError(toApiError(err).detail)
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
    filters.includeScenarioAttacks,
    filters.operator,
    filters.operationId,
    filters.otherLabels,
    pageRequest,
  ])

  const handleNextPage = () => {
    if (nextCursor) {
      fetchAttacks(nextCursor, pageRequest.page + 1)
    }
  }

  const handlePrevPage = () => {
    fetchAttacks(undefined, 0)
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
    !filters.includeScenarioAttacks ||
    filters.operator.length > 0 || filters.operationId.length > 0 || filters.otherLabels.length > 0
  // Rows and pagination are only shown once the current request has settled,
  // so the page label always describes the rows on screen.
  const displayLoading = settledRequestId !== pageRequest.requestId
  const emptyStateGuidance = activeTarget
    ? {
        text: 'Start an attack to see it here.',
        label: 'Start attack',
        icon: <ChatRegular />,
        view: 'chat' as const,
      }
    : {
        text: 'Set an objective default in the registry, or select a target directly in Chat.',
        label: 'Configure target',
        icon: <SettingsRegular />,
        view: 'registry' as const,
      }

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <div className={styles.headerRow}>
          {showTitle && <Text as="h1" size={500} weight="semibold">Attack History</Text>}
          <Button
            className={styles.touchTargetHeight}
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            onClick={() => fetchAttacks(undefined, 0)}
            disabled={displayLoading}
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
        {displayLoading ? (
          <div className={styles.emptyState}>
            <Spinner size="medium" label="Loading attacks..." />
          </div>
        ) : error ? (
          <div className={styles.emptyState} data-testid="error-state">
            <MessageBar intent="error">
              <MessageBarBody>{error}</MessageBarBody>
            </MessageBar>
            <Button
              className={styles.touchTargetHeight}
              appearance="primary"
              icon={<ArrowSyncRegular />}
              onClick={() => fetchAttacks(undefined, 0)}
              disabled={displayLoading}
              data-testid="retry-btn"
            >
              Retry
            </Button>
          </div>
        ) : attacks.length === 0 ? (
          <div className={styles.emptyState} data-testid="empty-state">
            <Text size={400}>No attacks found</Text>
            <Text size={200}>
              {hasActiveFilters ? 'Try adjusting your filters.' : emptyStateGuidance.text}
            </Text>
            {!hasActiveFilters && (
              <Button
                className={styles.touchTargetHeight}
                appearance="primary"
                icon={emptyStateGuidance.icon}
                onClick={() => onNavigate(emptyStateGuidance.view)}
              >
                {emptyStateGuidance.label}
              </Button>
            )}
          </div>
        ) : (
          <AttackTable attacks={attacks} onOpenAttack={onOpenAttack} formatDate={formatDate} />
        )}
      </div>

      {!displayLoading && attacks.length > 0 && (
        <HistoryPagination
          page={pageRequest.page}
          isLastPage={isLastPage}
          onPrevPage={handlePrevPage}
          onNextPage={handleNextPage}
        />
      )}
    </div>
  )
}
