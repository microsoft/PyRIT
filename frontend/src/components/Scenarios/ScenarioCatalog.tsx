import { type ReactNode, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'

import {
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
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { RegisteredScenario, ScenarioDatasetSummary } from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'

import { useScenarioCatalogStyles } from './ScenarioCatalog.styles'
import {
  ScenarioRunEstimateSummary,
} from './ScenarioRunEstimate'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'
import { techniqueSetName } from './scenarioTechniqueSets'

/** Items requested per catalog page while paging through the full list. */
const CATALOG_PAGE_SIZE = 200
const DESCRIPTION_OVERFLOW_TOLERANCE_PX = 2
function matchesSearch(scenario: RegisteredScenario, query: string): boolean {
  if (!query) {
    return true
  }
  const haystack = [
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
      dataset.selection_note ?? '',
      ...dataset.configured_caps.map((cap) => cap.label),
    ]),
  ]
    .join(' ')
    .toLowerCase()
  return haystack.includes(query.toLowerCase())
}

function uniqueNames(names: string[]): string[] {
  return [...new Set(names)]
}

function formatCount(value: number): string {
  return value.toLocaleString()
}

function formatObjectiveCount(value: number): string {
  return `${formatCount(value)} objective${value === 1 ? '' : 's'}`
}

function DefaultDatasetSummary({
  datasets,
  declaredDatasets,
  calculating,
}: {
  datasets: ScenarioDatasetSummary[]
  declaredDatasets: string[]
  calculating: boolean
}) {
  const styles = useScenarioCatalogStyles()

  if (calculating) {
    return <Spinner size="tiny" label="Calculating..." labelPosition="after" />
  }

  if (datasets.length === 0 && declaredDatasets.length === 0) {
    return <Text weight="semibold">No default datasets</Text>
  }

  if (datasets.length === 0) {
    return (
      <div className={styles.compactStack}>
        <Text weight="semibold">Population counts unavailable</Text>
        <Text size={200} className={styles.secondaryText}>{declaredDatasets.join(' · ')}</Text>
      </div>
    )
  }

  const objectiveCount = datasets.reduce(
    (total, dataset) => total + dataset.selected_seed_group_count,
    0,
  )
  const datasetNames = declaredDatasets.length > 0
    ? declaredDatasets
    : datasets.map((dataset) => dataset.name)

  return (
    <div className={styles.compactStack}>
      <Text weight="semibold">{formatObjectiveCount(objectiveCount)}</Text>
      <Text size={200} className={styles.secondaryText}>{datasetNames.join(' · ')}</Text>
    </div>
  )
}

interface ScenarioCatalogRowProps {
  scenario: RegisteredScenario
  estimatesLoading: boolean
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

function ScenarioCatalogRow({ scenario, estimatesLoading }: ScenarioCatalogRowProps) {
  const styles = useScenarioCatalogStyles()
  const descriptionId = useId()
  const techniquesId = useId()
  const [expanded, setExpanded] = useState(false)
  const [descriptionClipped, setDescriptionClipped] = useState(false)
  const [techniquesClipped, setTechniquesClipped] = useState(false)
  const defaultConcreteTechniques = uniqueNames(scenario.default_techniques)
  const estimateState = mapScenarioRunEstimate(scenario.default_run_size, 'default')
  const scenarioPath = `/scanner/${encodeURIComponent(scenario.scenario_name)}`
  const descriptionMarkdown = normalizeScenarioMarkdown(
    scenario.description_markdown || scenario.description,
  )
  const techniqueNames = defaultConcreteTechniques.join(' · ')
  const handleDescriptionClippedChange = useCallback((clipped: boolean) => {
    setDescriptionClipped(clipped)
  }, [])
  const handleTechniquesClippedChange = useCallback((clipped: boolean) => {
    setTechniquesClipped(clipped)
  }, [])
  const rowCanExpand = descriptionClipped || techniquesClipped

  return (
    <TableRow
      className={styles.summaryRow}
      data-testid={`scenario-card-${scenario.scenario_name}`}
    >
      <TableCell
        className={mergeClasses(styles.tableCell, styles.tableCellPadding, 'scenario-catalog-cell-padding')}
      >
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          Scenario / purpose
        </Text>
        <div className={styles.scenarioSummary}>
          <Link to={scenarioPath} className={styles.scenarioLink}>
            {scenario.scenario_name}
          </Link>
          <CollapsibleContent
            contentId={descriptionId}
            contentKey={descriptionMarkdown}
            expanded={expanded}
            collapsedClassName={styles.purposePreviewCollapsed}
            onClippedChange={handleDescriptionClippedChange}
          >
            <MarkdownContent
              content={descriptionMarkdown}
              className={styles.purposePreview}
              testId={`scenario-description-${scenario.scenario_name}`}
            />
          </CollapsibleContent>
          {rowCanExpand && (
            <Button
              appearance="transparent"
              size="small"
              className={styles.descriptionToggle}
              icon={expanded ? <ChevronUpRegular /> : <ChevronDownRegular />}
              aria-controls={`${descriptionId} ${techniquesId}`}
              aria-expanded={expanded}
              aria-label={`${expanded ? 'Collapse' : 'Expand'} row details for ${scenario.scenario_name}`}
              onClick={() => setExpanded((current) => !current)}
            />
          )}
        </div>
      </TableCell>
      <TableCell
        className={mergeClasses(styles.tableCell, styles.tableCellPadding, 'scenario-catalog-cell-padding')}
      >
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          Default datasets
        </Text>
        <DefaultDatasetSummary
          datasets={scenario.default_run_size.datasets}
          declaredDatasets={scenario.default_datasets}
          calculating={estimatesLoading}
        />
      </TableCell>
      <TableCell
        className={mergeClasses(styles.tableCell, styles.tableCellPadding, 'scenario-catalog-cell-padding')}
      >
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          Default techniques
        </Text>
        {defaultConcreteTechniques.length === 0 ? (
          <Text weight="semibold">No default techniques</Text>
        ) : (
          <div className={styles.compactStack}>
            <Text weight="semibold">
              {defaultConcreteTechniques.length} technique{defaultConcreteTechniques.length === 1 ? '' : 's'}
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
        )}
      </TableCell>
      <TableCell
        className={mergeClasses(styles.tableCell, styles.tableCellPadding, 'scenario-catalog-cell-padding')}
      >
        <Text className={styles.mobileLabel} size={200} weight="semibold">
          Default run size
        </Text>
        {estimatesLoading ? (
          <Spinner size="tiny" label="Calculating..." labelPosition="after" />
        ) : (
          <ScenarioRunEstimateSummary state={estimateState} compact />
        )}
      </TableCell>
    </TableRow>
  )
}

export default function ScenarioCatalog() {
  const styles = useScenarioCatalogStyles()
  const [scenarios, setScenarios] = useState<RegisteredScenario[]>([])
  const [loading, setLoading] = useState(true)
  const [estimatesLoading, setEstimatesLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [refetchCount, setRefetchCount] = useState(0)

  useEffect(() => {
    let cancelled = false

    const loadCatalog = async (): Promise<void> => {
      try {
        const items = await fetchAllPages(
          (cursor) => scenariosApi.listCatalog(CATALOG_PAGE_SIZE, cursor, false),
          undefined,
          (scenario) => scenario.scenario_name,
        )
        if (cancelled) return
        setScenarios(items)
        setError(null)
        setLoading(false)

        if (items.length === 0) {
          setEstimatesLoading(false)
          return
        }

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
      } catch (err: unknown) {
        if (cancelled) return
        setScenarios([])
        setError(toApiError(err).detail)
        setLoading(false)
        setEstimatesLoading(false)
      }
    }

    void loadCatalog()

    return () => {
      cancelled = true
    }
  }, [refetchCount])

  const handleRetry = useCallback(() => {
    setLoading(true)
    setEstimatesLoading(true)
    setError(null)
    setEstimateError(null)
    setRefetchCount((count) => count + 1)
  }, [])

  const filteredScenarios = useMemo(
    () => scenarios.filter((scenario) => matchesSearch(scenario, query)),
    [scenarios, query],
  )

  return (
    <section
      className={styles.root}
      data-testid="scenario-catalog"
      aria-labelledby="scenario-catalog-title"
    >
      <div className={styles.header}>
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

      {estimateError && (
        <MessageBar intent="warning">
          <MessageBarBody>Default run sizes could not be calculated: {estimateError}</MessageBarBody>
        </MessageBar>
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
      ) : filteredScenarios.length === 0 ? (
        <div className={styles.centeredState} data-testid="no-results-state">
          <Text size={400}>No scenarios match &quot;{query}&quot;</Text>
          <Text size={200}>Try a different search term.</Text>
        </div>
      ) : (
        <div className={styles.tableContainer}>
          <Table className={styles.table} size="small" aria-label="Registered scenarios">
            <TableHeader className={styles.tableHeader}>
              <TableRow>
                <TableHeaderCell
                  className={mergeClasses(
                    styles.scenarioColumn,
                    styles.tableHeaderCell,
                    'scenario-catalog-cell-padding',
                  )}
                >
                  Scenario / purpose
                </TableHeaderCell>
                <TableHeaderCell
                  className={mergeClasses(
                    styles.datasetColumn,
                    styles.tableHeaderCell,
                    'scenario-catalog-cell-padding',
                  )}
                >
                  Default datasets
                </TableHeaderCell>
                <TableHeaderCell
                  className={mergeClasses(
                    styles.techniqueColumn,
                    styles.tableHeaderCell,
                    'scenario-catalog-cell-padding',
                  )}
                >
                  Default techniques
                </TableHeaderCell>
                <TableHeaderCell
                  className={mergeClasses(
                    styles.sizeColumn,
                    styles.tableHeaderCell,
                    'scenario-catalog-cell-padding',
                  )}
                >
                  Default run size
                </TableHeaderCell>
              </TableRow>
            </TableHeader>
            <TableBody className={styles.tableBody}>
              {filteredScenarios.map((scenario) => (
                <ScenarioCatalogRow
                  key={scenario.scenario_name}
                  scenario={scenario}
                  estimatesLoading={estimatesLoading}
                />
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  )
}
