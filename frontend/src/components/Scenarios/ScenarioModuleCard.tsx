import { Spinner } from '@fluentui/react-components'

import CollapsibleCard from '@/components/CollapsibleCard'
import MarkdownContent from '@/components/Markdown/MarkdownContent'
import type { RegisteredScenario } from '@/types'

import ScenarioCatalogTable, { DatasetSummary, ScannerRow, type ScannerColumnLabels } from './ScenarioCatalogTable'
import { useScenarioModuleCardStyles } from './ScenarioModuleCard.styles'
import { ScenarioRunEstimateSummary } from './ScenarioRunEstimate'
import { normalizeScenarioMarkdown } from './scenarioMarkdown'
import { type ScenarioModuleGroup, shortScenarioName } from './scenarioModules'
import type { ScenarioLaunchNavigationState } from './scenarioRunNavigation'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'

/** The Launch table shows what each scenario runs by default. */
const SCENARIO_DEFAULT_COLUMNS: ScannerColumnLabels = {
  scenario: 'Scenario / purpose',
  datasets: 'Default datasets',
  techniques: 'Default techniques',
  runSize: 'Default run size',
}

function scenarioCount(count: number): string {
  return `${count} scenario${count === 1 ? '' : 's'}`
}

interface ScenarioLaunchRowProps {
  scenario: RegisteredScenario
  estimatesLoading: boolean
  linkState: ScenarioLaunchNavigationState
}

function ScenarioLaunchRow({ scenario, estimatesLoading, linkState }: ScenarioLaunchRowProps) {
  const styles = useScenarioModuleCardStyles()
  const descriptionMarkdown = normalizeScenarioMarkdown(
    scenario.description_markdown || scenario.description,
  )

  return (
    <ScannerRow
      columns={SCENARIO_DEFAULT_COLUMNS}
      testId={`scenario-card-${scenario.scenario_name}`}
      name={shortScenarioName(scenario)}
      nameTitle={scenario.scenario_name}
      path={`/scanner/launch/${encodeURIComponent(scenario.scenario_name)}`}
      linkState={linkState}
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

interface ScenarioModuleCardProps {
  group: ScenarioModuleGroup
  /** The scenarios to list: the whole module, or the ones matching a search. */
  scenarios: RegisteredScenario[]
  open: boolean
  onOpenChange: (open: boolean) => void
  estimatesLoading: boolean
  /** Carried by each scenario link so the configure page can return to this view. */
  linkState: ScenarioLaunchNavigationState
}

/** One module on the Launch tab: its scenarios in a collapsible card, each row opening the scenario. */
export default function ScenarioModuleCard({
  group,
  scenarios,
  open,
  onOpenChange,
  estimatesLoading,
  linkState,
}: ScenarioModuleCardProps) {
  const total = group.scenarios.length
  const meta = scenarios.length === total ? scenarioCount(total) : `${scenarios.length} of ${scenarioCount(total)}`

  return (
    <CollapsibleCard
      title={group.title}
      meta={meta}
      description={group.description}
      open={open}
      onOpenChange={onOpenChange}
      testId={`scenario-module-${group.key}`}
    >
      <ScenarioCatalogTable label={`Scenarios in ${group.title}`} columns={SCENARIO_DEFAULT_COLUMNS}>
        {scenarios.map((scenario) => (
          <ScenarioLaunchRow
            key={scenario.scenario_name}
            scenario={scenario}
            estimatesLoading={estimatesLoading}
            linkState={linkState}
          />
        ))}
      </ScenarioCatalogTable>
    </CollapsibleCard>
  )
}
