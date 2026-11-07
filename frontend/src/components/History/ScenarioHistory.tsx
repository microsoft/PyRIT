import { type MouseEvent, useCallback, useEffect, useRef, useState } from 'react'

import {
  Badge,
  Button,
  Combobox,
  Field,
  Input,
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
  DismissRegular,
  FilterDismissRegular,
  PlayRegular,
  ScriptRegular,
  SearchRegular,
} from '@fluentui/react-icons'

import { useScenarioQueue } from '@/hooks/useScenarioQueue'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { useScenarioRunResume } from '@/hooks/useScenarioRunResume'
import { labelsApi, operationsApi, scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  FilterOption, ScenarioQueueSnapshot, ScenarioRunListItem, ScenarioRunState, ScenarioRunSummary,
} from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'
import SearchableMultiCombobox from '@/components/SearchableMultiCombobox'
import { scenarioRunRoutePath, UUID_PATTERN } from '@/utils/routeParams'

import type { ViewName } from '../Sidebar/Navigation'
import { useScenarioHistoryStyles } from './ScenarioHistory.styles'
import {
  DEFAULT_SCENARIO_HISTORY_FILTERS,
  SCENARIO_RUN_STATES,
  type ScenarioHistoryFilters,
} from './scenarioHistoryFilters'

const PAGE_SIZE = 25
const INVALID_ID_MESSAGE = 'Enter a complete ID (UUID).'

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
  const [idDraft, setIdDraft] = useState({
    source: filters.referenceId, value: filters.referenceId, error: '',
  })
  if (idDraft.source !== filters.referenceId) {
    setIdDraft({ source: filters.referenceId, value: filters.referenceId, error: '' })
  }
  const validReference = !filters.referenceId || UUID_PATTERN.test(filters.referenceId)
  const idError = idDraft.error || (!validReference ? INVALID_ID_MESSAGE : '')
  const submitId = (value: string): void => {
    const id = value.trim()
    if (id && !UUID_PATTERN.test(id)) {
      setIdDraft((current) => ({ ...current, error: INVALID_ID_MESSAGE }))
      return
    }
    setIdDraft({ source: filters.referenceId, value: id, error: '' })
    onFiltersChange({ ...filters, referenceId: id.toLowerCase() })
  }
  const queue = useScenarioQueue()
  const lastQueueRevisionRef = useRef<number | null>(null)
  const [runs, setRuns] = useState<ScenarioRunListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const pending = useRef(false)
  const optionsPending = useRef(false)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  const [scenarioOptions, setScenarioOptions] = useState<string[]>([])
  const [optionsRevision, setOptionsRevision] = useState(0)
  const [operatorOptions, setOperatorOptions] = useState<string[]>([])
  const [operationOptions, setOperationOptions] = useState<FilterOption[]>([])
  const [otherLabelOptions, setOtherLabelOptions] = useState<string[]>([])
  const [page, setPage] = useState(0)
  const [nextCursor, setNextCursor] = useState<string | undefined>()
  const [hasMore, setHasMore] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const filterKey = JSON.stringify([
    filters.scenarioNames,
    filters.statuses,
    filters.operator,
    filters.operationId,
    filters.otherLabels,
    filters.referenceId,
  ])
  const [settledFilterKey, setSettledFilterKey] = useState<string | null>(null)
  const [fetchToken, setFetchToken] = useState({
    cursor: undefined as string | undefined,
    filterKey,
    nonce: 0,
    quiet: false,
  })
  if (fetchToken.filterKey !== filterKey) {
    setFetchToken({ cursor: undefined, filterKey, nonce: fetchToken.nonce + 1, quiet: false })
  }
  const queueRevision = queue.snapshot?.revision

  const requestPage = useCallback((cursor?: string, quiet = false) => {
    if (!quiet) {
      setLoading(true)
      setError(null)
    }
    setFetchToken((previous) => ({ cursor, filterKey, nonce: previous.nonce + 1, quiet }))
  }, [filterKey])
  const resume = useScenarioRunResume({
    onResumed: (resumedRun: ScenarioRunSummary): void => {
      setRuns((current: ScenarioRunListItem[]) => current.map((run: ScenarioRunListItem) =>
        run.scenario_result_id === resumedRun.scenario_result_id
          ? { ...run, status: resumedRun.status, completed_at: resumedRun.completed_at }
          : run,
      ))
    },
    onRefresh: (): void => {
      requestPage(fetchToken.filterKey === filterKey ? fetchToken.cursor : undefined, true)
      queue.retry()
    },
  })

  useEffect(() => {
    let cancelled = false
    optionsPending.current = true
    Promise.allSettled([
      fetchAllPages((cursor) => scenariosApi.listCatalog(100, cursor)),
      labelsApi.getLabels('scenarios'),
      operationsApi.list(),
    ]).then(([catalogResult, labelsResult, operationsResult]) => {
      if (cancelled) return
      const failures: string[] = []
      if (catalogResult.status === 'fulfilled') {
        setScenarioOptions(catalogResult.value.map((scenario) => scenario.scenario_name).sort())
      } else {
        failures.push(toApiError(catalogResult.reason).detail)
      }
      if (labelsResult.status === 'fulfilled') {
        const operators = labelsResult.value.labels.operator ?? []
        const others = Object.entries(labelsResult.value.labels)
          .filter(([key]) => key !== 'operator' && key !== 'operation' && key !== 'source')
          .flatMap(([key, values]) => values.map((value) => `${key}:${value}`))
        setOperatorOptions([...operators].sort())
        setOtherLabelOptions(others.sort())
      } else {
        failures.push(toApiError(labelsResult.reason).detail)
      }
      if (operationsResult.status === 'fulfilled') {
        setOperationOptions(operationsResult.value.items.map(operation => ({ value: operation.id, label: operation.name })))
      } else {
        failures.push(toApiError(operationsResult.reason).detail)
      }
      setOptionsError(failures.length > 0 ? failures.join(' ') : null)
      optionsPending.current = false
    })
    return () => {
      cancelled = true
      optionsPending.current = false
    }
  }, [optionsRevision])

  useEffect(() => {
    if (!runs.some((run) => !isTerminal(run.status))) return
    const interval = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(interval)
  }, [runs])

  useEffect(() => {
    if (!validReference) return
    let cancelled = false
    pending.current = true
    const effectiveCursor = fetchToken.filterKey === filterKey ? fetchToken.cursor : undefined
    const label = [
      ...filters.operator.map((value) => `operator:${value}`),
      ...filters.otherLabels,
    ]
    scenariosApi.listRuns({
      limit: PAGE_SIZE,
      cursor: effectiveCursor,
      scenario_names: filters.scenarioNames.length > 0 ? filters.scenarioNames : undefined,
      run_statuses: filters.statuses.length > 0 ? filters.statuses : undefined,
      label: label.length > 0 ? label : undefined,
      operation_id: filters.operationId.length > 0 ? filters.operationId : undefined,
      reference_id: filters.referenceId || undefined,
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
      if (!fetchToken.quiet) {
        setRuns([])
        setHasMore(false)
        setNextCursor(undefined)
      }
      setSettledFilterKey(filterKey)
      setError(toApiError(requestError).detail)
      if (!fetchToken.quiet && !effectiveCursor) setPage(0)
    }).finally(() => {
      if (!cancelled) {
        setLoading(false)
        pending.current = false
      }
    })
    return () => {
      cancelled = true
      pending.current = false
    }
  }, [
    fetchToken,
    filterKey,
    filters.scenarioNames,
    filters.statuses,
    filters.operator,
    filters.operationId,
    filters.otherLabels,
    filters.referenceId,
    validReference,
  ])

  useEffect(() => {
    if (queueRevision === undefined) return
    const previousRevision = lastQueueRevisionRef.current
    lastQueueRevisionRef.current = queueRevision
    if (previousRevision === null || previousRevision === queueRevision) return
    const currentCursor = fetchToken.filterKey === filterKey ? fetchToken.cursor : undefined
    requestPage(currentCursor, true)
  }, [fetchToken.cursor, fetchToken.filterKey, filterKey, queueRevision, requestPage])

  const setFilter = <K extends keyof ScenarioHistoryFilters>(
    key: K,
    value: ScenarioHistoryFilters[K],
  ): void => {
    onFiltersChange({ ...filters, [key]: value })
  }
  const hasFilters = Boolean(filters.referenceId || idDraft.value)
    || filters.scenarioNames.length > 0
    || filters.statuses.length > 0
    || filters.operator.length > 0
    || filters.operationId.length > 0
    || filters.otherLabels.length > 0
  const filtersPending = settledFilterKey !== filterKey
  const displayLoading = validReference && (loading || filtersPending)
  const displayedRuns = validReference ? runs : []
  useAutoRefresh(() => {
    if (!pending.current) {
      if (!optionsPending.current) setOptionsRevision((revision: number) => revision + 1)
      requestPage(fetchToken.filterKey === filterKey ? fetchToken.cursor : undefined, true)
    }
  }, validReference && !displayLoading)

  return (
    <div className={styles.root}>
      <header className={styles.header}>
        {showTitle && <div className={styles.headerRow}>
          <Text as="h1" size={500} weight="semibold">Scanner History</Text>
        </div>}
        <div className={styles.filters} role="group" aria-label="Scanner history filters">
          <Tooltip content="Reset all filters" relationship="label">
            <Button
              className={styles.touchTargetHeight}
              appearance="subtle"
              size="small"
              icon={<FilterDismissRegular />}
              aria-label="Reset all filters"
              disabledFocusable={!hasFilters}
              onClick={() => {
                setIdDraft({ source: '', value: '', error: '' })
                onFiltersChange({ ...DEFAULT_SCENARIO_HISTORY_FILTERS })
              }}
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
          <SearchableMultiCombobox
            ariaLabel="Operation"
            placeholder="All operations"
            selectedOptions={filters.operationId}
            options={operationOptions}
            onSelect={(values) => setFilter('operationId', values)}
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
          <form
            role="search"
            aria-label="Scan history"
            className={styles.lookupForm}
            onSubmit={(event: React.FormEvent<HTMLFormElement>) => {
              event.preventDefault()
              submitId(idDraft.value)
            }}
          >
            <Field validationState={idError ? 'error' : 'none'} validationMessage={idError || undefined}>
              <Tooltip content="Search by conversation, attack, finding, or scenario ID." relationship="description">
                <Input
                  className={styles.lookupInput}
                  aria-label="ID"
                  placeholder="ID"
                  contentBefore={<SearchRegular />}
                  contentAfter={idDraft.value ? (
                    <Button className={styles.touchTarget} appearance="transparent" size="small"
                      icon={<DismissRegular />} aria-label="Clear ID search"
                      onClick={() => { submitId('') }} />
                  ) : undefined}
                  value={idDraft.value}
                  onChange={(_, data) => {
                    setIdDraft((current) => ({ ...current, value: data.value, error: '' }))
                    if (!data.value.trim() || UUID_PATTERN.test(data.value.trim())) submitId(data.value)
                  }}
                />
              </Tooltip>
            </Field>
          </form>
        </div>
        {optionsError && (
          <MessageBar intent="warning">
            <MessageBarBody>{optionsError}</MessageBarBody>
          </MessageBar>
        )}
        {(resume.error || resume.executionError) && (
          <MessageBar intent="error">
            <MessageBarBody>{resume.error || resume.executionError}</MessageBarBody>
          </MessageBar>
        )}
      </header>

      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      <div className={styles.content}>
        {displayLoading ? (
          <div className={styles.emptyState}><Spinner label="Loading scanner history..." /></div>
        ) : error && displayedRuns.length === 0 ? null
        : displayedRuns.length === 0 ? (
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
          <ScenarioHistoryTable
            runs={displayedRuns}
            queueSnapshot={queue.snapshot}
            onOpenRun={onOpenRun}
            onResume={resume.requestResume}
            resumingRunId={resume.pendingRunId}
            now={now}
          />
        )}
      </div>

      {!displayLoading && displayedRuns.length > 0 && (
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
    </div>
  )
}

interface ScenarioHistoryTableProps {
  runs: ScenarioRunListItem[]
  queueSnapshot: ScenarioQueueSnapshot | null
  onOpenRun: (scenarioResultId: string) => void
  onResume: (scenarioResultId: string) => void
  resumingRunId: string | null
  now: number
}

function ScenarioHistoryTable({
  runs, queueSnapshot, onOpenRun, onResume, resumingRunId, now,
}: ScenarioHistoryTableProps) {
  const styles = useScenarioHistoryStyles()
  return (
    <Table className={styles.table} aria-label="Scanner history" data-testid="scenario-history-table">
      <TableHeader>
        <TableRow>
          <TableHeaderCell>Scenario</TableHeaderCell>
          <TableHeaderCell>State</TableHeaderCell>
          <TableHeaderCell>Operator</TableHeaderCell>
          <TableHeaderCell>Operation</TableHeaderCell>
          <TableHeaderCell>Target</TableHeaderCell>
          <TableHeaderCell>Timing</TableHeaderCell>
          <TableHeaderCell>Attack Success</TableHeaderCell>
          <TableHeaderCell>Errors / retries</TableHeaderCell>
          <TableHeaderCell>Labels</TableHeaderCell>
          <TableHeaderCell>Actions</TableHeaderCell>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => {
          const attackProgress = formatAttackProgress(run, queueSnapshot)
          return (
            <TableRow
              key={run.scenario_result_id}
              className={styles.clickableRow}
              data-testid={`scenario-history-row-${run.scenario_result_id}`}
              onClick={() => onOpenRun(run.scenario_result_id)}
            >
            <TableCell>
              <a
                href={scenarioRunRoutePath(run.scenario_result_id)}
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
            <TableCell>
              <div className={styles.stackedCell}>
                <Badge appearance="outline">{formatHistoryState(run, queueSnapshot)}</Badge>
                {attackProgress && (
                  <Text size={200} className={styles.secondary}>{attackProgress}</Text>
                )}
              </div>
            </TableCell>
            <TableCell>{run.labels.operator ?? 'Unavailable'}</TableCell>
            <TableCell>{run.operation_name ?? 'Unavailable'}</TableCell>
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
            <TableCell>
              <div className={mergeClasses(styles.stackedCell, styles.nowrap)}>
                <Text>{formatTimestamp(run.created_at)}</Text>
                <Text size={200} className={styles.secondary}>{formatRuntime(run, now)}</Text>
              </div>
            </TableCell>
            <TableCell className={styles.nowrap}>
              {formatSuccess(run)}
            </TableCell>
            <TableCell className={styles.nowrap}>{run.error_attacks} / {run.total_retries}</TableCell>
            <TableCell>
              <div className={styles.badges}>
                {Object.entries(run.labels)
                  .filter(([key]) => key !== 'operator' && key !== 'operation')
                  .map(([key, value]) => (
                    <Badge key={key} appearance="tint" size="small">{key}: {value}</Badge>
                  ))}
              </div>
            </TableCell>
            <TableCell>
              {run.status === 'FAILED' && (
                <Button
                  className={styles.touchTarget}
                  icon={<PlayRegular />}
                  disabled={resumingRunId !== null}
                  aria-label={`Resume ${run.scenario_registry_name ?? run.scenario_name} run ${run.scenario_result_id}`}
                  onClick={(event: MouseEvent<HTMLButtonElement>) => {
                    event.stopPropagation()
                    onResume(run.scenario_result_id)
                  }}
                  data-testid={`scenario-history-resume-${run.scenario_result_id}`}
                >
                  {resumingRunId === run.scenario_result_id ? 'Resuming...' : 'Resume'}
                </Button>
              )}
            </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function formatState(value: string): string {
  return value.toLowerCase().replace(/_/g, ' ').replace(/^\w/, (letter: string) => letter.toUpperCase())
}

function formatHistoryState(run: ScenarioRunListItem, queueSnapshot: ScenarioQueueSnapshot | null): string {
  if (isTerminal(run.status)) {
    return formatState(run.status)
  }
  if (isRunInProgress(run, queueSnapshot)) {
    return 'In progress'
  }
  const position = queueSnapshot?.queued.find(
    (entry) => entry.scenario_result_id === run.scenario_result_id,
  )?.position
  if (typeof position === 'number' && Number.isInteger(position) && position > 0) {
    return `Queued ${formatOrdinal(position)}`
  }
  return formatState(run.status)
}

function formatOrdinal(value: number): string {
  const lastTwoDigits = value % 100
  if (lastTwoDigits >= 11 && lastTwoDigits <= 13) {
    return `${value}th`
  }
  const suffix = value % 10 === 1 ? 'st' : value % 10 === 2 ? 'nd' : value % 10 === 3 ? 'rd' : 'th'
  return `${value}${suffix}`
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
  if (!run.started_at) {
    return run.status === 'CREATED' || run.status === 'QUEUED'
      ? 'Not started'
      : 'Execution time unavailable'
  }
  const start = Date.parse(run.started_at)
  const terminal = isTerminal(run.status)
  const end = terminal
    ? Date.parse(run.completed_at ?? run.updated_at)
    : now
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return 'Execution time unavailable'
  }
  const seconds = Math.max(0, Math.floor((end - start) / 1000))
  const duration = seconds < 60
    ? `${seconds}s`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)}m`
      : `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  return duration
}

function isTerminal(status: ScenarioRunState): boolean {
  return status === 'COMPLETED' || status === 'FAILED' || status === 'CANCELLED'
}

function isRunInProgress(run: ScenarioRunListItem, queueSnapshot: ScenarioQueueSnapshot | null): boolean {
  return run.status === 'IN_PROGRESS'
    || queueSnapshot?.active?.scenario_result_id === run.scenario_result_id
}

function formatAttackProgress(
  run: ScenarioRunListItem,
  queueSnapshot: ScenarioQueueSnapshot | null,
): string | null {
  if (isTerminal(run.status)) {
    return run.planned_total_available !== false
      && run.total_attacks !== null
      && run.completed_attacks !== run.total_attacks
      ? `${run.completed_attacks}/${run.total_attacks} attacks complete`
      : null
  }
  const inProgress = isRunInProgress(run, queueSnapshot)
  if (run.planned_total_available === false || run.total_attacks === null) {
    return inProgress
      ? `${run.completed_attacks} complete / total unknown`
      : 'Attack total unknown'
  }
  if (inProgress) {
    return `${run.completed_attacks}/${run.total_attacks} attacks complete`
  }
  const attackLabel = run.total_attacks === 1 ? 'attack' : 'attacks'
  return `${run.total_attacks} ${attackLabel} planned`
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
