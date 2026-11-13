import { ProgressBar, Text } from '@fluentui/react-components'

import type { ScenarioProgressCounts, ScenarioProgressHeader } from '@/types'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'
import ScenarioRunTiming from './ScenarioRunTiming'

interface ScenarioRunProgressProps {
  readonly run: ScenarioProgressHeader
  readonly overall: ScenarioProgressCounts
}

/** Queue position or completed attacks, with elapsed time and ETA, while a scan is active. */
export default function ScenarioRunProgress({ run, overall }: ScenarioRunProgressProps) {
  const styles = useScenarioRunPageStyles()
  const status = run.status === 'QUEUED'
    ? `Queued${run.queue_position ? ` · Position ${run.queue_position}` : ''}`
    : `${overall.completed}${overall.planned === null ? '' : ` of ${overall.planned}`} attacks complete`
  return (
    <section className={styles.progressSurface} aria-label="Scan progress">
      <div className={styles.progressPrimary}>
        <Text weight="semibold">{status}</Text>
        <ProgressBar value={overall.planned ? overall.completed / overall.planned : undefined} aria-label="Scan progress" />
      </div>
      <ScenarioRunTiming run={run} overall={overall} />
    </section>
  )
}
