import { Button, Spinner, Text, Tooltip } from '@fluentui/react-components'
import { PlayRegular } from '@fluentui/react-icons'

import CollapsibleCard from '@/components/CollapsibleCard'
import ScenarioCatalogTable, {
  DatasetSummary,
  ScannerRow,
  type ScannerColumnLabels,
} from '@/components/Scenarios/ScenarioCatalogTable'
import { ScenarioRunEstimateSummary } from '@/components/Scenarios/ScenarioRunEstimate'
import { mapScenarioRunEstimate } from '@/components/Scenarios/scenarioRunEstimateAdapter'
import type { ScenarioSuiteResponse } from '@/types'

import { useSuiteCardStyles } from './SuiteCard.styles'
import {
  formatAttackCount,
  formatScenarioCount,
  memberDatasetNames,
  memberDescription,
  memberScenarioName,
  memberTechniqueNames,
  suiteTotalAttacks,
  type SuiteMember,
} from './suiteMembers'

/** Each scenario in a preset runs with its own pinned settings, so the columns drop "Default". */
const MEMBER_COLUMNS: ScannerColumnLabels = {
  scenario: 'Scenario / purpose',
  datasets: 'Datasets',
  techniques: 'Techniques',
  runSize: 'Run size',
}

interface MemberRowProps {
  member: SuiteMember
  estimatesLoading: boolean
}

function MemberRow({ member, estimatesLoading }: MemberRowProps) {
  const styles = useSuiteCardStyles()
  const runSize = member.preset?.run_size ?? null
  const description = memberDescription(member)

  return (
    <ScannerRow
      columns={MEMBER_COLUMNS}
      testId={`suite-member-${member.entryId}`}
      name={memberScenarioName(member)}
      path={null}
      summary={(
        <Text size={200} className={styles.secondaryText}>Saved as {member.presetName}</Text>
      )}
      description={description === null ? null : (
        <Text size={200} className={styles.purpose}>{description}</Text>
      )}
      descriptionKey={description ?? ''}
      datasets={(
        <DatasetSummary
          datasets={runSize?.datasets ?? []}
          declaredDatasets={memberDatasetNames(member)}
          datasetSize={runSize?.dataset_size ?? null}
        />
      )}
      techniques={memberTechniqueNames(member)}
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

interface SuiteCardProps {
  item: ScenarioSuiteResponse
  members: SuiteMember[]
  estimatesLoading: boolean
  /** Why Launch is unavailable, shown in its tooltip; null when the preset can launch. */
  launchBlockedReason: string | null
  onLaunch: (item: ScenarioSuiteResponse) => void
}

/** A launchable preset in the Scanner: what it runs and one button to launch it all. */
export default function SuiteCard({ item, members, estimatesLoading, launchBlockedReason, onLaunch }: SuiteCardProps) {
  const styles = useSuiteCardStyles()
  const { suite } = item
  const total = estimatesLoading ? null : suiteTotalAttacks(members)
  const meta = total === null
    ? formatScenarioCount(members.length)
    : `${formatScenarioCount(members.length)} · ${formatAttackCount(total)}`
  const launchButton = (
    <Button
      className={styles.launch}
      appearance="primary"
      icon={<PlayRegular />}
      disabledFocusable={launchBlockedReason !== null}
      aria-label={`Launch ${suite.name}`}
      onClick={() => onLaunch(item)}
      data-testid={`launch-suite-${suite.name}`}
    >
      Launch
    </Button>
  )

  return (
    <CollapsibleCard
      title={suite.name}
      meta={meta}
      description={suite.description}
      testId={`suite-card-${suite.name}`}
      actions={launchBlockedReason === null ? launchButton : (
        <Tooltip relationship="description" content={launchBlockedReason}>
          {launchButton}
        </Tooltip>
      )}
    >
      {members.length === 0 ? (
        <Text size={200}>This preset has no scenarios yet.</Text>
      ) : (
        <ScenarioCatalogTable label={`Scenarios in ${suite.name}`} columns={MEMBER_COLUMNS}>
          {members.map((member) => (
            <MemberRow key={member.entryId} member={member} estimatesLoading={estimatesLoading} />
          ))}
        </ScenarioCatalogTable>
      )}
    </CollapsibleCard>
  )
}
