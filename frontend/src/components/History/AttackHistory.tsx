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
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const pending = useRef(false)
  const optionsPending = useRef(false)

  // Filter options
  const [attackTypeOptions, setAttackTypeOptions] = useState<string[]>([])
  const [optionsRevision, setOptionsRevision] = useState(0)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  const [narrowedOptionsError, setNarrowedOptionsError] = useState<{
    filterKey: string
    error: string | null
  } | null>(null)
  const [converterOptions, setConverterOptions] = useState<string[]>([])
  const [operatorOptions, setOperatorOptions] = useState<string[]>([])
  const [operationOptions, setOperationOptions] = useState<FilterOption[]>([])
  const [allOtherLabelOptions, setAllOtherLabelOptions] = useState<string[]>([])
  const [narrowedOtherLabelOptions, setNarrowedOtherLabelOptions] = useState<{
    filterKey: string
    options: string[]
  } | null>(null)

  // Pagination
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [isLastPage, setIsLastPage] = useState(true)
  const [page, setPage] = useState(0)
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
  const [settledFilterKey, setSettledFilterKey] = useState<string | null>(null)
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

  const [fetchToken, setFetchToken] = useState({
    cursor: undefined as string | undefined,
    filterKey,
    nonce: 0,
    quiet: false,
  })
  if (fetchToken.filterKey !== filterKey) {
    setFetchToken({ cursor: undefined, filterKey, nonce: fetchToken.nonce + 1, quiet: false })
  }

  const fetchAttacks = useCallback((pageCursor?: string, quiet = false) => {
    if (!quiet) {
      setLoading(true)
      setError(null)
    }
    setFetchToken(prev => ({ cursor: pageCursor, filterKey, nonce: prev.nonce + 1, quiet }))
  }, [filterKey])

  // Attack and converter options do not depend on the active history filters.
  useEffect(() => {
    let cancelled = false
    let failed = false
    optionsPending.current = true
    const fail = (cause: unknown): void => {
      failed = true
      if (!cancelled) setOptionsError(toApiError(cause).detail)
    }
    const reads = [attacksApi.getAttackOptions()
      .then(resp => { if (!cancelled) setAttackTypeOptions(resp.attack_types) })
      .catch(fail),
    attacksApi.getConverterOptions()
      .then(resp => { if (!cancelled) setConverterOptions(resp.converter_types) })
      .catch(fail),
    labelsApi.getLabels()
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
  }, [optionsRevision])

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
  ])

  // Fetch attacks whenever filters change or an event handler bumps fetchToken.
  // All setState calls live in .then/.catch/.finally so we don't trigger
  // react-hooks/set-state-in-effect.
  useEffect(() => {
    let cancelled = false
    pending.current = true
    const effectiveCursor = fetchToken.filterKey === filterKey && settledFilterKey === filterKey
      ? fetchToken.cursor
      : undefined
    attacksApi.listAttacks(buildListParams(filters, effectiveCursor))
      .then(response => {
        if (cancelled) return
        setAttacks(response.items.map(attack => ({ ...attack, labels: attack.labels ?? {} })))
        setIsLastPage(!response.pagination.has_more)
        setCursor(response.pagination.next_cursor ?? undefined)
        setSettledFilterKey(filterKey)
        setError(null)
        // Reset displayed page index when the trigger is a filter change (no
        // explicit cursor). Pagination handlers pass an explicit cursor and
        // update `page` themselves.
        if (!effectiveCursor) setPage(0)
      })
      .catch(err => {
        if (cancelled) return
        if (!fetchToken.quiet) setAttacks([])
        setSettledFilterKey(filterKey)
        setError(toApiError(err).detail)
        if (!fetchToken.quiet && !effectiveCursor) setPage(0)
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false)
          pending.current = false
        }
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
    filters.attackTypes,
    filters.outcome,
    filters.converter,
    filters.converterMatchMode,
    filters.hasConverters,
    filters.includeScenarioAttacks,
    filters.operator,
    filters.operationId,
    filters.otherLabels,
    filterKey,
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
    !filters.includeScenarioAttacks ||
    filters.operator.length > 0 || filters.operationId.length > 0 || filters.otherLabels.length > 0
  const filtersPending = settledFilterKey !== filterKey
  const displayLoading = loading || filtersPending
  useAutoRefresh(() => {
    if (!pending.current) {
      if (!optionsPending.current) setOptionsRevision((revision: number) => revision + 1)
      fetchAttacks(fetchToken.filterKey === filterKey ? fetchToken.cursor : undefined, true)
    }
  }, !displayLoading)
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
          attackTypeOptions={attackTypeOptions}
          converterOptions={converterOptions}
          operatorOptions={operatorOptions}
          operationOptions={operationOptions}
          otherLabelOptions={otherLabelOptions}
        />
        {displayedOptionsError && <MessageBar intent="error"><MessageBarBody>{displayedOptionsError}</MessageBarBody></MessageBar>}
      </div>

      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      <div className={styles.content}>
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
          <AttackTable attacks={attacks} onOpenAttack={onOpenAttack} formatDate={formatDate} />
        )}
      </div>

      {!displayLoading && attacks.length > 0 && (
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
