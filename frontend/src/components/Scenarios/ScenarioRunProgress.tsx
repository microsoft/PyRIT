import { mergeClasses, ProgressBar, Text } from '@fluentui/react-components'

import type { ScenarioActiveConversation, ScenarioProgressCounts, ScenarioProgressHeader } from '@/types'
import { isTerminalRunState } from '@/utils/scenarioRunProgress'

import AttackActivity from './AttackActivity'
import LiveAttacksPopover from './LiveAttacksPopover'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'
import type { LiveAttack } from './scenarioRunLayout'
import ScenarioRunTiming from './ScenarioRunTiming'

interface ScenarioRunProgressProps {
  readonly scenarioResultId: string
  readonly run: ScenarioProgressHeader
  readonly overall: ScenarioProgressCounts
  /** All running attacks remain available even when a matching attempt is shown inline. */
  readonly liveAttacks?: readonly LiveAttack[]
  /** Router state carried to a live conversation, so its back link returns here. */
  readonly conversationState: unknown
  readonly onOpenAttack: (conversation: ScenarioActiveConversation, trigger: HTMLElement) => void
}

/**
 * Queue position or completed attacks, timing, and live attacks while a scan is active.
 * It stays mounted and collapses when the scan finishes, so the results below do not jump.
 */
export default function ScenarioRunProgress({
  scenarioResultId, run, overall, liveAttacks, conversationState, onOpenAttack,
}: ScenarioRunProgressProps) {
  const styles = useScenarioRunPageStyles()
  const active = !isTerminalRunState(run.status)
  const queued = run.status === 'QUEUED'
  const status = queued
    ? `Queued${run.queue_position ? ` · Position ${run.queue_position}` : ''}`
    : `${overall.completed}${overall.planned === null ? '' : ` of ${overall.planned}`} attacks complete`
  return (
    <div className={mergeClasses(styles.progressRegion, !active && styles.progressRegionComplete)}
      aria-hidden={!active} inert={!active}>
      <div className={styles.progressClip}>
        <section className={styles.progressSurface} aria-label="Scan progress">
          <div className={styles.progressPrimary}>
            <AttackActivity label={queued ? 'Waiting to start' : 'Scan in progress'} />
            <Text weight="semibold">{status}</Text>
            <ProgressBar value={overall.planned ? overall.completed / overall.planned : undefined} aria-label="Scan progress" />
          </div>
          <div className={styles.progressTiming}>
            <ScenarioRunTiming run={run} />
            <ScenarioRunTiming run={run} overall={overall} />
          </div>
          {liveAttacks && <LiveAttacksPopover scenarioResultId={scenarioResultId} liveAttacks={liveAttacks}
            conversationState={conversationState} onOpenAttack={onOpenAttack} />}
        </section>
      </div>
    </div>
  )
}
