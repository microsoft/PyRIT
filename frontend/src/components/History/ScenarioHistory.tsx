import { useCallback, useEffect, useState } from 'react'

import {
  Badge,
  Button,
  Combobox,
  MessageBar,
  MessageBarBody,
  mergeClasses,
  Option,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
  Tooltip,
} from '@fluentui/react-components'
import {
  ArrowLeftRegular,
  ArrowRightRegular,
  ArrowSyncRegular,
  FilterDismissRegular,
  ScriptRegular,
} from '@fluentui/react-icons'

import { labelsApi, scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioRunListItem, ScenarioRunState } from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'

import type { ViewName } from '../Sidebar/Navigation'
import { useScenarioHistoryStyles } from './ScenarioHistory.styles'
import {
  DEFAULT_SCENARIO_HISTORY_FILTERS,
  SCENARIO_RUN_STATES,
  type ScenarioHistoryFilters,
} from './scenarioHistoryFilters'

const PAGE_SIZE = 25

interface ScenarioHistoryProps {
  filters: ScenarioHistoryFilters
  onFiltersChange: (filters: ScenarioHistoryFilters) => void
  onOpenRun: (scenarioResultId: string) => void
  onNavigate: (view: ViewName) => void
  showTitle?: boolean
}

interface MultiFilterProps {
  label: string
  placeholder: string
  selected: string[]
  options: readonly string[]
  onSelect: (values: string[]) => void
  testId: string
  className: string
}

function MultiFilter({
  label,
  placeholder,
  selected,
  options,
  onSelect,
  testId,
  className,
}: MultiFilterProps) {
  return (
    <Combobox
      aria-label={label}
      className={className}
      placeholder={placeholder}
      multiselect
      selectedOptions={selected}
      value={selected.length === 0 ? '' : selected.length === 1 ? selected[0] : `${selected[0]} (+${selected.length - 1})`}
      onOptionSelect={(_event, data) => onSelect(data.selectedOptions)}
      data-testid={testId}
    >
      {options.map((option) => <Option key={option} value={option}>{formatState(option)}</Option>)}
    </Combobox>
  )
}

export default function ScenarioHistory({
  filters,
  onFiltersChange,
  onOpenRun,
  onNavigate,
  showTitle = true,
}: ScenarioHistoryProps) {
  const styles = useScenarioHistoryStyles()
  const [runs, setRuns] = useState<ScenarioRunListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  const [scenarioOptions, setScenarioOptions] = useState<string[]>([])
  const [operatorOptions, setOperatorOptions] = useState<string[]>([])
  const [operationOptions, setOperationOptions] = useState<string[]>([])
  const [otherLabelOptions, setOtherLabelOptions] = useState<string[]>([])
  const [page, setPage] = useState(0)
  const [nextCursor, setNextCursor] = useState<string | undefined>()
  const [hasMore, setHasMore] = useState(false)
  const [now, setNow] = useState(0)
  const filterKey = JSON.stringify([
    filters.scenarioNames,
    filters.statuses,
    filters.operator,
    filters.operation,
    filters.otherLabels,
  ])
  const [settledFilterKey, setSettledFilterKey] = useState<string | null>(null)
  const [fetchToken, setFetchToken] = useState({
    cursor: undefined as string | undefined,
    filterKey,
    nonce: 0,
  })

  const requestPage = useCallback((cursor?: string) => {
    setLoading(true)
    setError(null)
    setFetchToken((previous) => ({ cursor, filterKey, nonce: previous.nonce + 1 }))
  }, [filterKey])

  useEffect(() => {
    let cancelled = false
    Promise.allSettled([
      fetchAllPages((cursor) => scenariosApi.listCatalog(100, cursor)),
      labelsApi.getLabels('scenarios'),
    ]).then(([catalogResult, labelsResult]) => {
      if (cancelled) return
      const failures: string[] = []
      if (catalogResult.status === 'fulfilled') {
        setScenarioOptions(catalogResult.value.map((scenario) => scenario.scenario_name).sort())
      } else {
        failures.push('scenario names')
      }
      if (labelsResult.status === 'fulfilled') {
        const operators = labelsResult.value.labels.operator ?? []
        const operations = labelsResult.value.labels.operation ?? []
        const others = Object.entries(labelsResult.value.labels)
          .filter(([key]) => key !== 'operator' && key !== 'operation' && key !== 'source')
          .flatMap(([key, values]) => values.map((value) => `${key}:${value}`))
        setOperatorOptions([...operators].sort())
        setOperationOptions([...operations].sort())
        setOtherLabelOptions(others.sort())
      } else {
        failures.push('labels')
      }
      setOptionsError(failures.length > 0 ? `Some filter options could not be loaded: ${failures.join(', ')}.` : null)
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!runs.some((run) => !isTerminal(run.status))) return
    const interval = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(interval)
  }, [runs])

  useEffect(() => {
    let cancelled = false
    const effectiveCursor = fetchToken.filterKey === filterKey ? fetchToken.cursor : undefined
    const label = [
      ...filters.operator.map((value) => `operator:${value}`),
      ...filters.operation.map((value) => `operation:${value}`),
      ...filters.otherLabels,
    ]
    scenariosApi.listRuns({
      limit: PAGE_SIZE,
      cursor: effectiveCursor,
      scenario_names: filters.scenarioNames.length > 0 ? filters.scenarioNames : undefined,
      run_statuses: filters.statuses.length > 0 ? filters.statuses : undefined,
      label: label.length > 0 ? label : undefined,
    }).then((response) => {
      if (cancelled) return
      setRuns(response.items)
      setNow(Date.now())
      setHasMore(response.pagination.has_more)
      setNextCursor(response.pagination.next_cursor ?? undefined)
      setSettledFilterKey(filterKey)
      setError(null)
      if (!effectiveCursor) setPage(0)
    }).catch((requestError: unknown) => {
      if (cancelled) return
      setRuns([])
      setHasMore(false)
      setNextCursor(undefined)
      setSettledFilterKey(filterKey)
      setError(toApiError(requestError).detail)
      if (!effectiveCursor) setPage(0)
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [
    fetchToken,
    filterKey,
    filters.scenarioNames,
    filters.statuses,
    filters.operator,
    filters.operation,
    filters.otherLabels,
  ])

  const setFilter = <K extends keyof ScenarioHistoryFilters>(
    key: K,
    value: ScenarioHistoryFilters[K],
  ): void => {
    onFiltersChange({ ...filters, [key]: value })
  }
  const hasFilters = filters.scenarioNames.length > 0
    || filters.statuses.length > 0
    || filters.operator.length > 0
    || filters.operation.length > 0
    || filters.otherLabels.length > 0
  const filtersPending = settledFilterKey !== filterKey
  const displayLoading = loading || filtersPending

  return (
    <main className={styles.root}>
      <header className={styles.header}>
        <div className={styles.headerRow}>
          {showTitle && <Text as="h1" size={500} weight="semibold">Scanner History</Text>}
          <Button
            className={styles.touchTargetHeight}
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            onClick={() => requestPage()}
            disabled={displayLoading}
            data-testid="scenario-history-refresh"
          >
            Refresh
          </Button>
        </div>
        <div className={styles.filters}>
          <Tooltip content="Reset all filters" relationship="label">
            <Button
              className={styles.touchTargetHeight}
              appearance="subtle"
              size="small"
              icon={<FilterDismissRegular />}
              aria-label="Reset all filters"
              disabled={!hasFilters}
              onClick={() => onFiltersChange({ ...DEFAULT_SCENARIO_HISTORY_FILTERS })}
              data-testid="scenario-reset-filters-btn"
            />
          </Tooltip>
          <MultiFilter
            label="Registered scenario"
            placeholder="All scenarios"
            selected={filters.scenarioNames}
            options={scenarioOptions}
            onSelect={(values) => setFilter('scenarioNames', values)}
            testId="scenario-filter"
            className={styles.filterDropdown}
          />
          <MultiFilter
            label="Run status"
            placeholder="All statuses"
            selected={filters.statuses}
            options={SCENARIO_RUN_STATES}
            onSelect={(values) => setFilter('statuses', values as ScenarioRunState[])}
            testId="scenario-status-filter"
            className={styles.filterDropdown}
          />
          <MultiFilter
            label="Operator"
            placeholder="All operators"
            selected={filters.operator}
            options={operatorOptions}
            onSelect={(values) => setFilter('operator', values)}
            testId="scenario-operator-filter"
            className={styles.filterDropdown}
          />
          <MultiFilter
            label="Operation"
            placeholder="All operations"
            selected={filters.operation}
            options={operationOptions}
            onSelect={(values) => setFilter('operation', values)}
            testId="scenario-operation-filter"
            className={styles.filterDropdown}
          />
          <MultiFilter
            label="Other labels"
            placeholder="Other labels"
            selected={filters.otherLabels}
            options={otherLabelOptions}
            onSelect={(values) => setFilter('otherLabels', values)}
            testId="scenario-label-filter"
            className={styles.filterDropdown}
          />
        </div>
        {optionsError && (
          <MessageBar intent="warning">
            <MessageBarBody>{optionsError}</MessageBarBody>
          </MessageBar>
        )}
      </header>

      <div className={styles.content}>
        {displayLoading ? (
          <div className={styles.emptyState}><Spinner label="Loading scanner history..." /></div>
        ) : error ? (
          <div className={styles.emptyState} data-testid="scenario-history-error">
            <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>
            <Button appearance="primary" icon={<ArrowSyncRegular />} onClick={() => requestPage()}>
              Retry
            </Button>
          </div>
        ) : runs.length === 0 ? (
          <div className={styles.emptyState} data-testid="scenario-history-empty">
            <Text size={400}>No scenario runs found</Text>
            <Text>{hasFilters ? 'Try adjusting your filters.' : 'Launch a scenario to see its progress and results here.'}</Text>
            {!hasFilters && (
              <Button appearance="primary" icon={<ScriptRegular />} onClick={() => onNavigate('scenarios')}>
                Browse scenarios
              </Button>
            )}
          </div>
        ) : (
          <ScenarioHistoryTable runs={runs} onOpenRun={onOpenRun} now={now} />
        )}
      </div>

      {!displayLoading && !error && runs.length > 0 && (
        <div className={styles.pagination}>
          <Button
            className={styles.touchTarget}
            icon={<ArrowLeftRegular />}
            disabled={page === 0}
            onClick={() => {
              setPage(0)
              requestPage()
            }}
          >
            First
          </Button>
          <Text>Page {page + 1}</Text>
          <Button
            className={styles.touchTarget}
            icon={<ArrowRightRegular />}
            iconPosition="after"
            disabled={!hasMore || !nextCursor}
            onClick={() => {
              if (!nextCursor) return
              setPage((current) => current + 1)
              requestPage(nextCursor)
            }}
          >
            Next
          </Button>
        </div>
      )}
    </main>
  )
}

interface ScenarioHistoryTableProps {
  runs: ScenarioRunListItem[]
  onOpenRun: (scenarioResultId: string) => void
  now: number
}

function ScenarioHistoryTable({ runs, onOpenRun, now }: ScenarioHistoryTableProps) {
  const styles = useScenarioHistoryStyles()
  return (
    <Table className={styles.table} aria-label="Scanner history" data-testid="scenario-history-table">
      <TableHeader>
        <TableRow>
          <TableHeaderCell>Scenario</TableHeaderCell>
          <TableHeaderCell>State</TableHeaderCell>
          <TableHeaderCell>Target</TableHeaderCell>
          <TableHeaderCell>Created</TableHeaderCell>
          <TableHeaderCell>Runtime</TableHeaderCell>
          <TableHeaderCell>Attacks Complete</TableHeaderCell>
          <TableHeaderCell>Attack Success</TableHeaderCell>
          <TableHeaderCell>Errors / retries</TableHeaderCell>
          <TableHeaderCell>Labels</TableHeaderCell>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => (
          <TableRow
            key={run.scenario_result_id}
            className={styles.clickableRow}
            data-testid={`scenario-history-row-${run.scenario_result_id}`}
            onClick={() => onOpenRun(run.scenario_result_id)}
          >
            <TableCell>
              <a
                href={`/scanner-history/${run.scenario_result_id}`}
                className={styles.rowLink}
                aria-label={`Open ${run.scenario_registry_name ?? run.scenario_name} scenario run`}
                onClick={(event) => {
                  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
                    event.stopPropagation()
                    return
                  }
                  event.preventDefault()
                  event.stopPropagation()
                  onOpenRun(run.scenario_result_id)
                }}
              >
                <span className={styles.identity}>
                  <Text weight="semibold">{run.scenario_registry_name ?? run.scenario_name}</Text>
                  <Text size={200} className={styles.secondary}>
                    {run.scenario_registry_name && run.scenario_registry_name !== run.scenario_name
                      ? `${run.scenario_name} · v${run.scenario_version}`
                      : `v${run.scenario_version}`}
                  </Text>
                </span>
              </a>
            </TableCell>
            <TableCell><Badge appearance="outline">{formatState(run.status)}</Badge></TableCell>
            <TableCell>
              {run.target ? (
                <Tooltip content={run.target.endpoint ?? run.target.target_type} relationship="label">
                  <div className={styles.target}>
                    <Text>{run.target.model_name ?? run.target.target_type}</Text>
                    <Text size={200} className={mergeClasses(styles.secondary, styles.truncate)}>
                      {run.target.target_type}
                    </Text>
                  </div>
                </Tooltip>
              ) : 'Unavailable'}
            </TableCell>
            <TableCell className={styles.nowrap}>{formatTimestamp(run.created_at)}</TableCell>
            <TableCell className={styles.nowrap}>
              {formatRuntime(run, now)}
            </TableCell>
            <TableCell className={styles.nowrap}>
              {run.planned_total_available !== false && run.total_attacks !== null
                ? `${run.completed_attacks}/${run.total_attacks}`
                : `${run.completed_attacks} known / total unknown`}
            </TableCell>
            <TableCell className={styles.nowrap}>
              {formatSuccess(run)}
            </TableCell>
            <TableCell className={styles.nowrap}>{run.error_attacks} / {run.total_retries}</TableCell>
            <TableCell>
              <div className={styles.badges}>
                {Object.entries(run.labels).map(([key, value]) => (
                  <Badge key={key} appearance="tint" size="small">{key}: {value}</Badge>
                ))}
              </div>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function formatState(value: string): string {
  return value.toLowerCase().replace(/_/g, ' ').replace(/^\w/, (letter: string) => letter.toUpperCase())
}

function formatTimestamp(value: string): string {
  return new Date(value).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatRuntime(run: ScenarioRunListItem, now: number): string {
  const start = Date.parse(run.created_at)
  const terminal = isTerminal(run.status)
  const end = terminal
    ? Date.parse(run.completed_at ?? run.updated_at)
    : now
  const seconds = Math.max(0, Math.floor((end - start) / 1000))
  const duration = seconds < 60
    ? `${seconds}s`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)}m`
      : `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  return `${duration} (${terminal ? 'completed' : 'in progress'})`
}

function isTerminal(status: ScenarioRunState): boolean {
  return status === 'COMPLETED' || status === 'FAILED' || status === 'CANCELLED'
}

function formatSuccess(run: ScenarioRunListItem): string {
  const successful = run.successful_attacks
  if (run.planned_total_available === false) {
    return `${successful}/${run.completed_attacks} known results`
  }
  if (run.completed_attacks === 0) {
    return '0/0'
  }
  return `${successful}/${run.completed_attacks} (${run.objective_achieved_rate}%)`
}
