import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  Input,
  Link as FluentLink,
  MessageBar,
  MessageBarBody,
  Spinner,
  Text,
} from '@fluentui/react-components'
import { SearchRegular } from '@fluentui/react-icons'

import MarkdownContent from '@/components/Markdown/MarkdownContent'
import { useRuntime } from '@/hooks/useRuntime'
import { useAutoRefresh } from '@/hooks/useAutoRefresh'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { RegisteredScenario } from '@/types'
import { fetchAllPages } from '@/utils/fetchAllPages'

import { useScenarioCatalogStyles } from './ScenarioCatalog.styles'
import ScenarioCatalogTable, { DatasetSummary, ScannerRow, type ScannerColumnLabels } from './ScenarioCatalogTable'
import { ScenarioRunEstimateSummary } from './ScenarioRunEstimate'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'
import { techniqueSetName } from './scenarioTechniqueSets'

/** Items requested per catalog page while paging through the full list. */
const CATALOG_PAGE_SIZE = 200

/** The Launch table shows what each scenario runs by default. */
const SCENARIO_DEFAULT_COLUMNS: ScannerColumnLabels = {
  scenario: 'Scenario / purpose',
  datasets: 'Default datasets',
  techniques: 'Default techniques',
  runSize: 'Default run size',
}

type SearchTerm = string | null | undefined

function matchesQuery(terms: SearchTerm[], query: string): boolean {
  if (!query) {
    return true
  }
  return terms.join(' ').toLowerCase().includes(query.toLowerCase())
}

function compareNames(left: string, right: string): number {
  if (left < right) {
    return -1
  }
  return left > right ? 1 : 0
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
      columns={SCENARIO_DEFAULT_COLUMNS}
      testId={`scenario-card-${scenario.scenario_name}`}
      name={scenario.scenario_name}
      path={`/scanner/launch/${encodeURIComponent(scenario.scenario_name)}`}
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
      techniques={[...new Set(scenario.default_techniques)]}
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

/** The Scanner's Launch tab: every registered scenario, each opening its configure page. */
export default function ScenarioCatalog() {
  const { generation } = useRuntime()
  const styles = useScenarioCatalogStyles()
  const [scenarios, setScenarios] = useState<RegisteredScenario[]>([])
  const [loading, setLoading] = useState(true)
  const [estimatesLoading, setEstimatesLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [refetchCount, setRefetchCount] = useState(0)
  const pending = useRef(false)

  useEffect(() => {
    let cancelled = false
    pending.current = true

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

    const loadCatalog = async (): Promise<void> => {
      let items: RegisteredScenario[]
      try {
        items = await fetchAllPages(
          (cursor) => scenariosApi.listCatalog(CATALOG_PAGE_SIZE, cursor, false),
          undefined,
          (scenario) => scenario.scenario_name,
        )
      } catch (err: unknown) {
        if (cancelled) return
        setError(toApiError(err).detail)
        setLoading(false)
        setEstimatesLoading(false)
        return
      }
      if (cancelled) return

      setScenarios((current) => items.map((item) => {
        const previous = current.find((candidate) => candidate.scenario_name === item.scenario_name)
        return previous
          && JSON.stringify({ ...previous, default_run_size: null }) === JSON.stringify({ ...item, default_run_size: null })
          ? { ...item, default_run_size: previous.default_run_size }
          : item
      }))
      setError(null)
      setLoading(false)

      if (items.length === 0) {
        setEstimatesLoading(false)
        return
      }
      await loadScenarioEstimates()
    }

    void loadCatalog().finally(() => { if (!cancelled) pending.current = false })

    return () => {
      cancelled = true
      pending.current = false
    }
  }, [refetchCount, generation])

  const handleRetry = useCallback(() => {
    setRefetchCount((count) => count + 1)
  }, [])
  useAutoRefresh(() => { if (!pending.current) handleRetry() }, !loading)

  const filteredScenarios = useMemo(
    () => scenarios
      .filter((scenario) => matchesQuery(scenarioSearchTerms(scenario), query))
      .sort((left, right) => compareNames(left.scenario_name, right.scenario_name)),
    [scenarios, query],
  )

  return (
    <section
      className={styles.root}
      data-testid="scenario-catalog"
      aria-labelledby="scenario-catalog-title"
    >
      <div className={styles.header} data-tour="scanner-catalog">
        <div className={styles.headerText}>
          <Text id="scenario-catalog-title" as="h2" size={600} weight="semibold">
            <FluentLink
              href="https://microsoft.github.io/PyRIT/latest/scanner/scanner/"
              target="_blank"
              rel="noopener noreferrer"
            >
              Launch a scan
            </FluentLink>
          </Text>
          <Text size={300} className={styles.subtitle}>
            Select a scenario to configure a scan.
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
        </div>
      </div>

      {estimateError && (
        <div className={styles.messages}>
          <MessageBar intent="warning">
            <MessageBarBody>{estimateError}</MessageBarBody>
          </MessageBar>
        </div>
      )}

      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      {loading ? (
        <div className={styles.centeredState}>
          <Spinner label="Loading scenarios..." />
        </div>
      ) : error && scenarios.length === 0 ? null
      : scenarios.length === 0 ? (
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
        <ScenarioCatalogTable label="Scenarios" columns={SCENARIO_DEFAULT_COLUMNS}>
          {filteredScenarios.map((scenario) => (
            <ScenarioScannerRow
              key={scenario.scenario_name}
              scenario={scenario}
              estimatesLoading={estimatesLoading}
            />
          ))}
        </ScenarioCatalogTable>
      )}
    </section>
  )
}
