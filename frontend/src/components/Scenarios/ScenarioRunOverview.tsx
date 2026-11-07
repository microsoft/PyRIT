import { Badge, Text } from '@fluentui/react-components'
import { Link } from 'react-router'

import { formatProgressSuccess } from '@/components/AttackResults/attackAttemptFormatting'
import type { ScenarioProgressCounts, ScenarioProgressHeader } from '@/types'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'
import ScenarioRunTiming from './ScenarioRunTiming'

interface ScenarioRunOverviewProps {
  readonly run: ScenarioProgressHeader
  readonly overall: ScenarioProgressCounts
}

/** Operation, target, timing, success, errors, and labels for the whole scan. */
export default function ScenarioRunOverview({ run, overall }: ScenarioRunOverviewProps) {
  const styles = useScenarioRunPageStyles()
  const outcomes = overall.outcomes
  return (
    <section className={styles.metadata} aria-label="Scan summary">
      <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Operation</Text>
        {run.operation_id
          ? <Link to={`/operations/${encodeURIComponent(run.operation_id)}`}>{run.operation_name ?? 'Operation'}</Link>
          : <Text>Unassigned</Text>}
      </div>
      <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Target</Text>
        <Text weight="semibold">{run.target?.model_name ?? run.target?.target_type ?? 'Not recorded'}</Text>
      </div>
      <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Timing</Text>
        <ScenarioRunTiming run={run} />
      </div>
      <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Attack success</Text>
        <Text weight="semibold">{formatProgressSuccess(overall)}</Text>
        <Text size={200} className={styles.sectionHint}>
          {outcomes ? `${outcomes.successes} / ${outcomes.total_decided} decided` : `${overall.succeeded} / ${overall.completed} complete`}
        </Text>
      </div>
      <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Errors / retries</Text>
        <Text weight="semibold">{overall.errors} / {overall.retries}</Text>
      </div>
      <div className={styles.metadataItem}><Text size={200} className={styles.metadataLabel}>Labels</Text>
        <div className={styles.badgeList}>{Object.entries(run.labels ?? {}).map(([key, value]) => (
          <Badge key={key} appearance="outline" className={styles.labelBadge}>{key}: {value}</Badge>
        ))}</div>
      </div>
    </section>
  )
}
