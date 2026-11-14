import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Text,
  Button,
  Spinner,
  MessageBar,
  MessageBarBody,
} from '@fluentui/react-components'
import { ChatRegular } from '@fluentui/react-icons'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useRuntime } from '@/hooks/useRuntime'
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
  targets?: TargetInstance[]
  targetsError?: string | null
  onNavigate: (view: ViewName) => void
  showTitle?: boolean
}

const PAGE_SIZE = 25

type ListParams = Parameters<typeof attacksApi.listAttacks>[0]

/**
 * One history page request. The cursor and the displayed page index always
 * change together, and `listKey` records their runtime generation and filters.
 */
interface PageRequest {
  listKey: string
  cursor: string | undefined
  page: number
  requestId: number
  quiet: boolean
}

function buildListParams(filters: HistoryFilters, pageCursor: string | undefined): ListParams {
  const labelParams = [...filters.otherLabels]

  const params: ListParams = { limit: PAGE_SIZE }
  if (pageCursor) params.cursor = pageCursor
  if (filters.outcome) params.outcome = filters.outcome
  if (filters.operator.length > 0) params.operator = filters.operator
  if (filters.operationId.length > 0) params.operation_id = filters.operationId
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
  targets = [],
  targetsError,
  onNavigate,
  showTitle = true,
}: AttackHistoryProps) {
  const styles = useAttackHistoryStyles()
  const runtime = useRuntime()
  const [attacks, setAttacks] = useState<AttackSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const pending = useRef(false)
  const optionsPending = useRef(false)

  // Filter options
  const [optionsRevision, setOptionsRevision] = useState(0)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  const [narrowedOptionsError, setNarrowedOptionsError] = useState<{
    filterKey: string
    error: string | null
  } | null>(null)
  const [operatorOptions, setOperatorOptions] = useState<string[]>([])
  const [operationOptions, setOperationOptions] = useState<FilterOption[]>([])
  const [allOtherLabelOptions, setAllOtherLabelOptions] = useState<string[]>([])
  const [narrowedOtherLabelOptions, setNarrowedOtherLabelOptions] = useState<{
    filterKey: string
    options: string[]
  } | null>(null)
  const [optionsGeneration, setOptionsGeneration] = useState(runtime.generation)

  // Reset before paint so the new runtime never offers the previous runtime's options.
  if (optionsGeneration !== runtime.generation) {
    setOptionsGeneration(runtime.generation)
    setOperatorOptions([])
    setOperationOptions([])
    setAllOtherLabelOptions([])
    setNarrowedOtherLabelOptions(null)
    setOptionsError(null)
    setNarrowedOptionsError(null)
  }

  // Pagination
  const [nextCursor, setNextCursor] = useState<string | undefined>(undefined)
  const [isLastPage, setIsLastPage] = useState(true)
  const filterKey = JSON.stringify([
    filters.outcome,
    filters.includeScenarioAttacks,
    filters.operator,
    filters.operationId,
    filters.otherLabels,
  ])
  const listKey = JSON.stringify([runtime.generation, filterKey])
  const [pageRequest, setPageRequest] = useState<PageRequest>(() => ({
    listKey,
    cursor: undefined,
    page: 0,
    requestId: 0,
    quiet: false,
  }))
  const [settledRequestId, setSettledRequestId] = useState<number | null>(null)
  // Every runtime or filter change (including A -> B -> A while B is loading)
  // starts a new visit on the first page, so an earlier cursor is never reused.
  // Uses the "adjust state during render" pattern (see React docs:
  // https://react.dev/reference/react/useState#storing-information-from-previous-renders)
  // instead of a useEffect so we don't trigger react-hooks/set-state-in-effect.
  if (pageRequest.listKey !== listKey) {
    setPageRequest({ listKey, cursor: undefined, page: 0, requestId: pageRequest.requestId + 1, quiet: false })
    setError(null)
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
  const displayedOptionsError = hasLabelOptionFilters && narrowedOptionsError?.filterKey === labelOptionFilterKey
    ? narrowedOptionsError.error ?? optionsError
    : optionsError

  const fetchAttacks = useCallback((pageCursor: string | undefined, page: number, quiet = false) => {
    if (!quiet) setError(null)
    setPageRequest(prev => ({ ...prev, cursor: pageCursor, page, requestId: prev.requestId + 1, quiet }))
  }, [])

  useEffect(() => {
    if (!runtime.ready) return
    let cancelled = false
    let failed = false
    optionsPending.current = true
    const fail = (cause: unknown): void => {
      failed = true
      if (!cancelled) setOptionsError(toApiError(cause).detail)
    }
    const reads = [labelsApi.getLabels()
      .then(resp => {
        if (cancelled) return
        // TODO(PyRIT 1.4): Remove the labels.* fallbacks with legacy attribution aliases.
        setOperatorOptions([...(resp.operators ?? resp.labels.operator ?? [])].sort())
        setAllOtherLabelOptions(buildOtherLabelOptions(resp.labels))
      })
      .catch(fail),
    operationsApi.list()
      .then(resp => {
        if (!cancelled) setOperationOptions(resp.items.map(operation => ({ value: operation.id, label: operation.name })))
      })
      .catch(fail)]
    void Promise.allSettled(reads).then(() => {
      if (!cancelled) {
        optionsPending.current = false
        if (!failed) setOptionsError(null)
      }
    })
    return () => { cancelled = true; optionsPending.current = false }
  }, [optionsRevision, runtime.ready, runtime.generation])

  // Arbitrary label options are narrowed by the active indexed attribution filters.
  useEffect(() => {
    if (!runtime.ready || !hasLabelOptionFilters) return
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
        setNarrowedOptionsError({ filterKey: labelOptionFilterKey, error: null })
      })
      .catch((cause: unknown) => {
        if (!cancelled) setNarrowedOptionsError({ filterKey: labelOptionFilterKey, error: toApiError(cause).detail })
      })
    return () => {
      cancelled = true
    }
  }, [
    filters.operator,
    filters.otherLabels,
    hasLabelOptionFilters,
    labelOptionFilterKey,
    optionsRevision,
    runtime.ready,
    runtime.generation,
  ])

  // Fetch attacks whenever filters change or an event handler issues a new
  // page request. All setState calls live in .then/.catch so we don't trigger
  // react-hooks/set-state-in-effect.
  useEffect(() => {
    if (!runtime.ready) return
    let cancelled = false
    pending.current = true
    const { cursor, requestId, quiet } = pageRequest
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
        if (!quiet) setAttacks([])
        setSettledRequestId(requestId)
        setError(toApiError(err).detail)
      })
      .finally(() => {
        if (!cancelled) pending.current = false
      })
    return () => {
      cancelled = true
      pending.current = false
    }
    // The filter fields are listed individually rather than as `filters` so the
    // effect only re-runs when a meaningful sub-field changes (the parent
    // creates a new `filters` object on every render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    filters.outcome,
    filters.includeScenarioAttacks,
    filters.operator,
    filters.operationId,
    filters.otherLabels,
    pageRequest,
    runtime.ready,
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
    filters.outcome ||
    !filters.includeScenarioAttacks ||
    filters.operator.length > 0 || filters.operationId.length > 0 || filters.otherLabels.length > 0
  // Quiet refreshes retain the current rows and page; navigation waits for its
  // own request so the page label always describes the rows on screen.
  const displayLoading = !pageRequest.quiet && settledRequestId !== pageRequest.requestId
  useAutoRefresh(() => {
    if (!pending.current) {
      if (!optionsPending.current) setOptionsRevision((revision: number) => revision + 1)
      fetchAttacks(pageRequest.cursor, pageRequest.page, true)
    }
  }, runtime.ready && !displayLoading)
  const emptyStateGuidance = activeTarget
    ? {
        text: 'Start an attack to see it here.',
        label: 'Start attack',
        icon: <ChatRegular />,
        view: 'chat' as const,
      }
    : {
        text: 'Select a target in Chat to start an attack.',
        label: 'Open Chat',
        icon: <ChatRegular />,
        view: 'chat' as const,
      }

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        {showTitle && <div className={styles.headerRow}>
          <Text as="h1" size={500} weight="semibold">Attack History</Text>
        </div>}
        <HistoryFiltersBar
          filters={filters}
          onFiltersChange={onFiltersChange}
          operatorOptions={operatorOptions}
          operationOptions={operationOptions}
          otherLabelOptions={otherLabelOptions}
        />
        {displayedOptionsError && <MessageBar intent="error"><MessageBarBody>{displayedOptionsError}</MessageBarBody></MessageBar>}
        {targetsError && <MessageBar intent="error"><MessageBarBody>Target registry: {targetsError}</MessageBarBody></MessageBar>}
      </div>

      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      <div className={styles.content} data-testid="attack-history-scroll-region">
        {displayLoading ? (
          <div className={styles.emptyState}>
            <Spinner size="medium" label="Loading attacks..." />
          </div>
        ) : error && attacks.length === 0 ? null
        : attacks.length === 0 ? (
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
          <AttackTable attacks={attacks} targets={targets} onOpenAttack={onOpenAttack} formatDate={formatDate} />
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
