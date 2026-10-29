import { type ReactNode, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'

import {
  Badge,
  Button,
  Input,
  Link as FluentLink,
  mergeClasses,
  MessageBar,
  MessageBarBody,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHeaderCell,
  TableRow,
  Text,
} from '@fluentui/react-components'
import {
  ArrowSyncRegular,
  ChevronDownRegular,
  ChevronUpRegular,
  SearchRegular,
} from '@fluentui/react-icons'
import { Link } from 'react-router'

import MarkdownContent from '@/components/Markdown/MarkdownContent'
import { presetScannerRoutePath } from '@/components/ScenarioPresets/presetRoutes'
import { estimatePresetRunSizes } from '@/components/ScenarioPresets/presetRunSizes'
import { useRuntime } from '@/hooks/useRuntime'
import { scenarioPresetsApi, scenariosApi, scenarioSuitesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  RegisteredScenario,
  ScenarioDatasetSizeEstimate,
  ScenarioDatasetSummary,
  ScenarioPresetResponse,
  ScenarioSuiteResponse,
  TargetInstance,
} from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'

import ScannerSuiteSection from './ScannerSuiteSection'
import { SCANNER_CELL_PADDING_CLASS, useScenarioCatalogStyles } from './ScenarioCatalog.styles'
import { ScenarioRunEstimateSummary } from './ScenarioRunEstimate'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'
import { techniqueSetMembers, techniqueSetName } from './scenarioTechniqueSets'

/** Items requested per catalog page while paging through the full list. */
const CATALOG_PAGE_SIZE = 200
const DESCRIPTION_OVERFLOW_TOLERANCE_PX = 2

type SearchTerm = string | null | undefined

interface ScenarioEntry {
  kind: 'scenario'
  name: string
  scenario: RegisteredScenario
}

interface PresetEntry {
  kind: 'preset'
  name: string
  item: ScenarioPresetResponse
  /** Undefined when this deployment does not register the preset's scenario. */
  scenario: RegisteredScenario | undefined
}

type ScannerEntry = ScenarioEntry | PresetEntry

interface ScenarioCatalogProps {
  targets: TargetInstance[]
  defaultObjectiveTarget: TargetInstance | null
  defaultAdversarialTarget: TargetInstance | null
  labels: Record<string, string>
  /** The saved Operation that every suite child run joins, or null for none. */
  operationId: string | null
  /** False while server defaults or the global Operation selection are unresolved. */
  defaultsReady: boolean
}

function matchesQuery(terms: SearchTerm[], query: string): boolean {
  if (!query) {
    return true
  }
  return terms.join(' ').toLowerCase().includes(query.toLowerCase())
}

function uniqueNames(names: string[]): string[] {
  return [...new Set(names)]
}

function compareNames(left: string, right: string): number {
  if (left < right) {
    return -1
  }
  return left > right ? 1 : 0
}

function isListedInScanner(item: ScenarioPresetResponse): boolean {
  return item.preset.show_in_scanner !== false
}

/** The concrete techniques a preset runs, expanding any technique set it pins. */
function presetTechniqueNames(
  item: ScenarioPresetResponse,
  scenario: RegisteredScenario | undefined,
): string[] {
  const selections = item.preset.techniques
  if (!selections || selections.length === 0) {
    return uniqueNames(scenario?.default_techniques ?? [])
  }
  return uniqueNames(selections.flatMap((name) => {
    const members = scenario ? techniqueSetMembers(scenario, name) : []
    return members.length > 0 ? members : [name]
  }))
}

function presetDatasetNames(
  item: ScenarioPresetResponse,
  scenario: RegisteredScenario | undefined,
): string[] {
  const sources = item.preset.datasets?.sources ?? []
  return sources.length > 0
    ? sources.map((source) => source.name)
    : scenario?.default_datasets ?? []
}

function scenarioSearchTerms(scenario: RegisteredScenario): SearchTerm[] {
  return [
    scenario.scenario_name,
    scenario.description,
    scenario.description_markdown,
    scenario.scenario_type,
    scenario.default_technique,
    ...scenario.default_techniques,
    ...scenario.aggregate_techniques,
    ...scenario.aggregate_techniques.map(techniqueSetName),
    ...Object.values(scenario.aggregate_technique_expansions).flat(),
    ...scenario.all_techniques,
    ...scenario.default_datasets,
    ...scenario.default_run_size.datasets.flatMap((dataset) => [
      dataset.name,
      dataset.selection_note,
      ...dataset.configured_caps.map((cap) => cap.label),
    ]),
  ]
}

function presetSearchTerms(entry: PresetEntry): SearchTerm[] {
  const { preset } = entry.item
  return [
    preset.name,
    preset.description,
    preset.scenario_name,
    ...(preset.techniques ?? []),
    ...presetTechniqueNames(entry.item, entry.scenario),
    ...presetDatasetNames(entry.item, entry.scenario),
  ]
}

function suiteSearchTerms(
  item: ScenarioSuiteResponse,
  presetsByName: ReadonlyMap<string, ScenarioPresetResponse>,
): SearchTerm[] {
  return [
    item.suite.name,
    item.suite.description,
    ...item.suite.entries.flatMap((entry) => [
      entry.preset_name,
      presetsByName.get(entry.preset_name)?.preset.scenario_name,
    ]),
  ]
}

function formatCount(value: number): string {
  return value.toLocaleString()
}

function formatObjectiveCount(value: number): string {
  return `${formatCount(value)} objective${value === 1 ? '' : 's'}`
}

function formatSizeBound(size: ScenarioDatasetSizeEstimate): string {
  switch (size.kind) {
    case 'bounded':
      return `Up to ${formatObjectiveCount(size.value)}`
    case 'all_available':
      return 'All available data (configured child limits still apply)'
    case 'indeterminate':
      return size.detail
  }
}

interface DatasetSummaryProps {
  datasets: ScenarioDatasetSummary[]
  declaredDatasets: string[]
  /** Null while the size is unknown, which omits the size line. */
  datasetSize: ScenarioDatasetSizeEstimate | null
}

function DatasetSummary({ datasets, declaredDatasets, datasetSize }: DatasetSummaryProps) {
  const styles = useScenarioCatalogStyles()

  if (datasets.length === 0 && declaredDatasets.length === 0) {
    return <Text weight="semibold">No default datasets</Text>
  }

  const datasetNames = declaredDatasets.length > 0
    ? declaredDatasets
    : datasets.map((dataset) => dataset.name)

  return (
    <div className={styles.compactStack}>
      {datasetSize && <Text weight="semibold">{formatSizeBound(datasetSize)}</Text>}
      <Text size={200} className={styles.secondaryText}>{datasetNames.join(' · ')}</Text>
    </div>
  )
}

interface CollapsibleContentProps {
  children: ReactNode
  collapsedClassName: string
  contentId: string
  contentKey: string
  expanded: boolean
  onClippedChange: (clipped: boolean) => void
}

function CollapsibleContent({
  children,
  collapsedClassName,
  contentId,
  contentKey,
  expanded,
  onClippedChange,
}: CollapsibleContentProps) {
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const content = contentRef.current
    if (!content || expanded) {
      return
    }

    const updateClippedState = () => {
      onClippedChange(content.scrollHeight - content.clientHeight > DESCRIPTION_OVERFLOW_TOLERANCE_PX)
    }
    updateClippedState()

    const resizeObserver = new ResizeObserver(updateClippedState)
    resizeObserver.observe(content)
    return () => resizeObserver.disconnect()
  }, [contentKey, expanded, onClippedChange])

  return (
    <div
      id={contentId}
      ref={contentRef}
      className={expanded ? undefined : collapsedClassName}
    >
      {children}
    </div>
  )
}

interface ScannerRowProps {
  testId: string
  name: string
  /** Where the name links. Null renders the name as plain text. */
  path: string | null
  /** Always-visible details between the name and the collapsible description. */
  summary?: ReactNode
  description: ReactNode | null
  /** Changes with the description content so its overflow is measured again. */
  descriptionKey: string
  datasets: ReactNode
  techniques: string[]
  runSize: ReactNode
}

function ScannerRow({
  testId,
  name,
  path,
  summary,
  description,
  descriptionKey,
  datasets,
  techniques,
  runSize,
}: ScannerRowProps) {
  const styles = useScenarioCatalogStyles()
  const descriptionId = useId()
  const techniquesId = useId()
  const [expanded, setExpanded] = useState(false)
  const [descriptionClipped, setDescriptionClipped] = useState(false)
  const [techniquesClipped, setTechniquesClipped] = useState(false)
  const handleDescriptionClippedChange = useCallback((clipped: boolean) => {
    setDescriptionClipped(clipped)
  }, [])
  const handleTechniquesClippedChange = useCallback((clipped: boolean) => {
    setTechniquesClipped(clipped)
  }, [])
  const hasDescription = description !== null
  const hasTechniques = techniques.length > 0
  const techniqueNames = techniques.join(' · ')
  const rowCanExpand = (hasDescription && descriptionClipped) || (hasTechniques && techniquesClipped)
  const controlledIds = [
    ...(hasDescription ? [descriptionId] : []),
    ...(hasTechniques ? [techniquesId] : []),
  ].join(' ')
  const cellClassName = mergeClasses(
    styles.tableCell,
    styles.tableCellPadding,
    SCANNER_CELL_PADDING_CLASS,
  )

  return (
    <TableRow className={styles.summaryRow} data-testid={testId}>
      <TableCell className={cellClassName}>
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          Scenario / purpose
        </Text>
        <div className={styles.scenarioSummary}>
          {path === null ? (
            <Text weight="semibold">{name}</Text>
          ) : (
            <Link to={path} className={styles.scenarioLink}>
              {name}
            </Link>
          )}
          {summary}
          {hasDescription && (
            <CollapsibleContent
              contentId={descriptionId}
              contentKey={descriptionKey}
              expanded={expanded}
              collapsedClassName={styles.purposePreviewCollapsed}
              onClippedChange={handleDescriptionClippedChange}
            >
              {description}
            </CollapsibleContent>
          )}
          {rowCanExpand && (
            <Button
              appearance="transparent"
              size="small"
              className={styles.descriptionToggle}
              icon={expanded ? <ChevronUpRegular /> : <ChevronDownRegular />}
              aria-controls={controlledIds}
              aria-expanded={expanded}
              aria-label={`${expanded ? 'Collapse' : 'Expand'} row details for ${name}`}
              onClick={() => setExpanded((current) => !current)}
            />
          )}
        </div>
      </TableCell>
      <TableCell className={cellClassName}>
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          Default datasets
        </Text>
        {datasets}
      </TableCell>
      <TableCell className={cellClassName}>
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          Default techniques
        </Text>
        {hasTechniques ? (
          <div className={styles.compactStack}>
            <Text weight="semibold">
              {techniques.length} technique{techniques.length === 1 ? '' : 's'}
            </Text>
            <CollapsibleContent
              contentId={techniquesId}
              contentKey={techniqueNames}
              expanded={expanded}
              collapsedClassName={styles.inlineSummaryCollapsed}
              onClippedChange={handleTechniquesClippedChange}
            >
              <Text size={200} className={styles.secondaryText}>{techniqueNames}</Text>
            </CollapsibleContent>
          </div>
        ) : (
          <Text weight="semibold">No default techniques</Text>
        )}
      </TableCell>
      <TableCell className={cellClassName}>
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          Default run size
        </Text>
        {runSize}
      </TableCell>
    </TableRow>
  )
}

interface ScenarioScannerRowProps {
  scenario: RegisteredScenario
  estimatesLoading: boolean
}

function ScenarioScannerRow({ scenario, estimatesLoading }: ScenarioScannerRowProps) {
  const styles = useScenarioCatalogStyles()
  const descriptionMarkdown = normalizeScenarioMarkdown(
    scenario.description_markdown || scenario.description,
  )

  return (
    <ScannerRow
      testId={`scenario-card-${scenario.scenario_name}`}
      name={scenario.scenario_name}
      path={`/scanner/${encodeURIComponent(scenario.scenario_name)}`}
      description={(
        <MarkdownContent
          content={descriptionMarkdown}
          className={styles.purposePreview}
          testId={`scenario-description-${scenario.scenario_name}`}
        />
      )}
      descriptionKey={descriptionMarkdown}
      datasets={(
        <DatasetSummary
          datasets={scenario.default_run_size.datasets}
          declaredDatasets={scenario.default_datasets}
          datasetSize={scenario.default_run_size.dataset_size}
        />
      )}
      techniques={uniqueNames(scenario.default_techniques)}
      runSize={estimatesLoading ? (
        <Spinner size="tiny" label="Calculating..." labelPosition="after" />
      ) : (
        <ScenarioRunEstimateSummary
          state={mapScenarioRunEstimate(scenario.default_run_size, 'default')}
          compact
        />
      )}
    />
  )
}

interface PresetScannerRowProps {
  entry: PresetEntry
  estimatesLoading: boolean
}

function PresetScannerRow({ entry, estimatesLoading }: PresetScannerRowProps) {
  const styles = useScenarioCatalogStyles()
  const { item, scenario } = entry
  const { preset, issues } = item
  const runSize = item.run_size ?? null
  const runnable = issues.length === 0

  return (
    <ScannerRow
      testId={`preset-card-${preset.name}`}
      name={preset.name}
      path={scenario ? presetScannerRoutePath(preset.scenario_name, preset.name) : null}
      summary={(
        <>
          <div className={styles.presetMeta}>
            <Badge appearance="tint" color="brand">Preset</Badge>
            <Text size={200} className={styles.secondaryText}>
              Runs {preset.scenario_name}
            </Text>
            {!runnable && (
              <Badge appearance="tint" color="warning">
                Not runnable here
              </Badge>
            )}
          </div>
          {!runnable && (
            <ul className={styles.issueList} data-testid={`preset-issues-${preset.name}`}>
              {issues.map((issue) => (
                <li key={`${issue.field}:${issue.message}`}>
                  <Text size={200}>{issue.message}</Text>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      description={preset.description ? (
        <Text
          size={200}
          className={styles.purposePreview}
          data-testid={`preset-description-${preset.name}`}
        >
          {preset.description}
        </Text>
      ) : null}
      descriptionKey={preset.description ?? ''}
      datasets={(
        <DatasetSummary
          datasets={runSize?.datasets ?? []}
          declaredDatasets={presetDatasetNames(item, scenario)}
          datasetSize={runSize?.dataset_size ?? null}
        />
      )}
      techniques={presetTechniqueNames(item, scenario)}
      runSize={estimatesLoading ? (
        <Spinner size="tiny" label="Calculating..." labelPosition="after" />
      ) : runSize ? (
        <ScenarioRunEstimateSummary state={mapScenarioRunEstimate(runSize, 'request')} compact />
      ) : (
        <Text size={200} className={styles.secondaryText}>—</Text>
      )}
    />
  )
}

export default function ScenarioCatalog({
  targets,
  defaultObjectiveTarget,
  defaultAdversarialTarget,
  labels,
  operationId,
  defaultsReady,
}: ScenarioCatalogProps) {
  const { generation } = useRuntime()
  const styles = useScenarioCatalogStyles()
  const individualTitleId = useId()
  const [scenarios, setScenarios] = useState<RegisteredScenario[]>([])
  const [suites, setSuites] = useState<ScenarioSuiteResponse[]>([])
  const [presets, setPresets] = useState<ScenarioPresetResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [estimatesLoading, setEstimatesLoading] = useState(true)
  const [presetEstimatesLoading, setPresetEstimatesLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  const [presetEstimateError, setPresetEstimateError] = useState<string | null>(null)
  const [suitesError, setSuitesError] = useState<string | null>(null)
  const [presetsError, setPresetsError] = useState<string | null>(null)
  const [suiteNotice, setSuiteNotice] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [refetchCount, setRefetchCount] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false

    const loadScenarioEstimates = async (): Promise<void> => {
      try {
        const estimatedItems = await fetchAllPages(
          (cursor) => scenariosApi.listCatalog(CATALOG_PAGE_SIZE, cursor),
          undefined,
          (scenario) => scenario.scenario_name,
        )
        if (cancelled) return
        setScenarios(estimatedItems)
        setEstimateError(null)
      } catch (err: unknown) {
        if (cancelled) return
        setEstimateError(toApiError(err).detail)
      } finally {
        if (!cancelled) {
          setEstimatesLoading(false)
        }
      }
    }

    const loadPresetEstimates = async (listed: ScenarioPresetResponse[]): Promise<void> => {
      const { runSizes, failures } = await estimatePresetRunSizes(listed, controller.signal)
      if (cancelled) return
      setPresets((current) => current.map((item) => (
        runSizes.has(item.preset.name)
          ? { ...item, run_size: runSizes.get(item.preset.name) ?? null }
          : item
      )))
      setPresetEstimateError(failures.length > 0 ? failures.join('; ') : null)
      setPresetEstimatesLoading(false)
    }

    // Suites and presets are optional: the Scanner still lists scenarios when either fails.
    const loadCatalog = async (): Promise<void> => {
      const [catalogResult, suitesResult, presetsResult] = await Promise.allSettled([
        fetchAllPages(
          (cursor) => scenariosApi.listCatalog(CATALOG_PAGE_SIZE, cursor, false),
          undefined,
          (scenario) => scenario.scenario_name,
        ),
        scenarioSuitesApi.list(controller.signal),
        scenarioPresetsApi.list(controller.signal, false),
      ])
      if (cancelled) return

      if (catalogResult.status === 'rejected') {
        setScenarios([])
        setSuites([])
        setPresets([])
        setError(toApiError(catalogResult.reason).detail)
        setLoading(false)
        setEstimatesLoading(false)
        setPresetEstimatesLoading(false)
        return
      }

      const items = catalogResult.value
      const listedPresets = presetsResult.status === 'fulfilled'
        ? presetsResult.value.items.filter(isListedInScanner)
        : []
      setScenarios(items)
      setSuites(suitesResult.status === 'fulfilled' ? suitesResult.value.items : [])
      setPresets(presetsResult.status === 'fulfilled' ? presetsResult.value.items : [])
      setSuitesError(
        suitesResult.status === 'rejected' ? toApiError(suitesResult.reason).detail : null,
      )
      setPresetsError(
        presetsResult.status === 'rejected' ? toApiError(presetsResult.reason).detail : null,
      )
      setError(null)
      setLoading(false)

      if (items.length === 0) {
        setEstimatesLoading(false)
        setPresetEstimatesLoading(false)
        return
      }
      if (listedPresets.length === 0) {
        setPresetEstimatesLoading(false)
      }

      await Promise.all([
        loadScenarioEstimates(),
        listedPresets.length > 0 ? loadPresetEstimates(listedPresets) : Promise.resolve(),
      ])
    }

    void loadCatalog()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [refetchCount, generation])

  const handleRetry = useCallback(() => {
    setLoading(true)
    setEstimatesLoading(true)
    setPresetEstimatesLoading(true)
    setError(null)
    setEstimateError(null)
    setPresetEstimateError(null)
    setSuitesError(null)
    setPresetsError(null)
    setSuiteNotice(null)
    setRefetchCount((count) => count + 1)
  }, [])

  const handleSuiteChanged = useCallback((name: string) => {
    handleRetry()
    setSuiteNotice(
      `Suite '${name}' or one of its presets changed while you were launching it. `
      + 'Review it and launch again.',
    )
  }, [handleRetry])

  const presetsByName = useMemo(
    () => new Map(presets.map((item) => [item.preset.name, item])),
    [presets],
  )

  const entries = useMemo<ScannerEntry[]>(() => {
    const scenariosByName = new Map(scenarios.map((scenario) => [scenario.scenario_name, scenario]))
    const scenarioEntries = scenarios.map((scenario): ScannerEntry => ({
      kind: 'scenario',
      name: scenario.scenario_name,
      scenario,
    }))
    const presetEntries = presets.filter(isListedInScanner).map((item): ScannerEntry => ({
      kind: 'preset',
      name: item.preset.name,
      item,
      scenario: scenariosByName.get(item.preset.scenario_name),
    }))
    return [...scenarioEntries, ...presetEntries].sort(
      (left, right) => compareNames(left.name, right.name),
    )
  }, [scenarios, presets])

  const filteredEntries = useMemo(
    () => entries.filter((entry) => matchesQuery(
      entry.kind === 'scenario' ? scenarioSearchTerms(entry.scenario) : presetSearchTerms(entry),
      query,
    )),
    [entries, query],
  )

  const filteredSuites = useMemo(
    () => suites
      .filter((item) => matchesQuery(suiteSearchTerms(item, presetsByName), query))
      .sort((left, right) => compareNames(left.suite.name, right.suite.name)),
    [suites, presetsByName, query],
  )

  return (
    <section
      className={styles.root}
      data-testid="scenario-catalog"
      aria-labelledby="scenario-catalog-title"
    >
      <div className={styles.header} data-tour="scanner-catalog">
        <div className={styles.headerText}>
          <Text id="scenario-catalog-title" as="h1" size={600} weight="semibold">
            <FluentLink
              href="https://microsoft.github.io/PyRIT/latest/scanner/scanner/"
              target="_blank"
              rel="noopener noreferrer"
            >
              Scanner
            </FluentLink>
          </Text>
          <Text size={300} className={styles.subtitle}>
            Launch comprehensive testing campaigns with multiple attack techniques against a target.
          </Text>
        </div>
        <div className={styles.headerActions}>
          <Input
            className={styles.search}
            contentBefore={<SearchRegular />}
            placeholder="Search scenarios..."
            value={query}
            onChange={(_, data) => setQuery(data.value)}
            aria-label="Search scenarios"
          />
          <Button
            className={styles.touchTarget}
            appearance="subtle"
            icon={<ArrowSyncRegular />}
            onClick={handleRetry}
            disabled={loading}
          >
            Refresh
          </Button>
        </div>
      </div>

      {(suiteNotice || suitesError || presetsError || estimateError || presetEstimateError) && (
        <div className={styles.messages}>
          {suiteNotice && (
            <MessageBar intent="warning" data-testid="suite-changed-notice">
              <MessageBarBody>{suiteNotice}</MessageBarBody>
            </MessageBar>
          )}
          {suitesError && (
            <MessageBar intent="warning">
              <MessageBarBody>Scenario suites could not be loaded: {suitesError}</MessageBarBody>
            </MessageBar>
          )}
          {presetsError && (
            <MessageBar intent="warning">
              <MessageBarBody>Scenario presets could not be loaded: {presetsError}</MessageBarBody>
            </MessageBar>
          )}
          {estimateError && (
            <MessageBar intent="warning">
              <MessageBarBody>Default run sizes could not be calculated: {estimateError}</MessageBarBody>
            </MessageBar>
          )}
          {presetEstimateError && (
            <MessageBar intent="warning">
              <MessageBarBody>Preset run sizes could not be calculated: {presetEstimateError}</MessageBarBody>
            </MessageBar>
          )}
        </div>
      )}

      {loading ? (
        <div className={styles.centeredState}>
          <Spinner label="Loading scenarios..." />
        </div>
      ) : error ? (
        <div className={styles.centeredState} data-testid="error-state">
          <MessageBar intent="error">
            <MessageBarBody>{error}</MessageBarBody>
          </MessageBar>
          <Button
            className={styles.touchTarget}
            appearance="primary"
            icon={<ArrowSyncRegular />}
            onClick={handleRetry}
            data-testid="retry-btn"
          >
            Retry
          </Button>
        </div>
      ) : scenarios.length === 0 ? (
        <div className={styles.centeredState} data-testid="empty-state">
          <Text size={400}>No scenarios are registered</Text>
          <Text size={200}>Register a scenario via your PyRIT initializers to see it here.</Text>
        </div>
      ) : filteredSuites.length === 0 && filteredEntries.length === 0 ? (
        <div className={styles.centeredState} data-testid="no-results-state">
          <Text size={400}>No scenarios match &quot;{query}&quot;</Text>
          <Text size={200}>Try a different search term.</Text>
        </div>
      ) : (
        <div className={styles.sections}>
          {filteredSuites.length > 0 && (
            <ScannerSuiteSection
              suites={filteredSuites}
              presets={presetsByName}
              targets={targets}
              defaultObjectiveTarget={defaultObjectiveTarget}
              defaultAdversarialTarget={defaultAdversarialTarget}
              labels={labels}
              operationId={operationId}
              defaultsReady={defaultsReady}
              onSuiteChanged={handleSuiteChanged}
            />
          )}
          {filteredEntries.length > 0 && (
            <section
              className={styles.section}
              aria-labelledby={suites.length > 0 ? individualTitleId : undefined}
            >
              {suites.length > 0 && (
                <Text id={individualTitleId} as="h2" size={500} weight="semibold">
                  Individual scanners
                </Text>
              )}
              <div className={styles.tableContainer}>
                <Table className={styles.table} size="small" aria-label="Individual scanners">
                  <TableHeader className={styles.tableHeader}>
                    <TableRow>
                      <TableHeaderCell
                        className={mergeClasses(
                          styles.scenarioColumn,
                          styles.tableHeaderCell,
                          SCANNER_CELL_PADDING_CLASS,
                        )}
                      >
                        Scenario / purpose
                      </TableHeaderCell>
                      <TableHeaderCell
                        className={mergeClasses(
                          styles.datasetColumn,
                          styles.tableHeaderCell,
                          SCANNER_CELL_PADDING_CLASS,
                        )}
                      >
                        Default datasets
                      </TableHeaderCell>
                      <TableHeaderCell
                        className={mergeClasses(
                          styles.techniqueColumn,
                          styles.tableHeaderCell,
                          SCANNER_CELL_PADDING_CLASS,
                        )}
                      >
                        Default techniques
                      </TableHeaderCell>
                      <TableHeaderCell
                        className={mergeClasses(
                          styles.sizeColumn,
                          styles.tableHeaderCell,
                          SCANNER_CELL_PADDING_CLASS,
                        )}
                      >
                        Default run size
                      </TableHeaderCell>
                    </TableRow>
                  </TableHeader>
                  <TableBody className={styles.tableBody}>
                    {filteredEntries.map((entry) => (
                      entry.kind === 'scenario' ? (
                        <ScenarioScannerRow
                          key={`scenario:${entry.name}`}
                          scenario={entry.scenario}
                          estimatesLoading={estimatesLoading}
                        />
                      ) : (
                        <PresetScannerRow
                          key={`preset:${entry.name}`}
                          entry={entry}
                          estimatesLoading={presetEstimatesLoading}
                        />
                      )
                    ))}
                  </TableBody>
                </Table>
              </div>
            </section>
          )}
        </div>
      )}
    </section>
  )
}
