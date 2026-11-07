import { useMemo } from 'react'

import { Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle } from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'

import AttackAttemptDetails from '@/components/AttackResults/AttackAttemptDetails'
import type { ScenarioRunAttempt } from '@/hooks/useScenarioRunAttackDetails'
import type {
  ScenarioActiveConversation,
  ScenarioHierarchyNode,
  ScenarioProgressSummary,
  ScenarioRunPlan,
} from '@/types'
import { attackConversationRoutePath, scenarioConversationRoutePath } from '@/utils/routeParams'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

interface AttackDetailsDialogProps {
  readonly scenarioResultId: string
  readonly attempt: ScenarioRunAttempt | null
  /** The hierarchy node the dialog was opened from, which may carry the objective. */
  readonly hierarchySelection: ScenarioHierarchyNode | null
  readonly plan: ScenarioRunPlan | null
  readonly summary: ScenarioProgressSummary
  readonly activeConversations: readonly ScenarioActiveConversation[]
  readonly seedObjectives: Map<string, string>
  /** Router state carried to the conversation, so its back link returns here. */
  readonly conversationState: unknown
  readonly onClose: () => void
}

/** One attack's objective, outcome, technique, scorer, and conversation, opened from anywhere on the run page. */
export default function AttackDetailsDialog({
  scenarioResultId, attempt, hierarchySelection, plan, summary, activeConversations, seedObjectives, conversationState, onClose,
}: AttackDetailsDialogProps) {
  const styles = useScenarioRunPageStyles()
  const plannedAtomicGroups = useMemo(
    () => new Map(plan?.atomic_groups.map((group) => [group.id, group]) ?? []),
    [plan],
  )
  const progressAtomicGroups = useMemo(
    () => new Map(summary.atomic_groups.map((group) => [group.id, group])),
    [summary],
  )
  const atomicGroupId = attempt && 'atomic_group_id' in attempt ? attempt.atomic_group_id : null
  const active = attempt ? activeConversations.find((conversation) => conversation.attack_result_id === attempt.attack_result_id) : undefined
  const objective = attempt
    ? (hierarchySelection?.attack_result_id === attempt.attack_result_id ? hierarchySelection.objective : null) ??
      ('objective' in attempt ? attempt.objective : null) ??
      (attempt.seed_group_id ? seedObjectives.get(attempt.seed_group_id) : null) ??
      null
    : null
  const conversationPath = !attempt ? null
    : active ? scenarioConversationRoutePath(scenarioResultId, active.conversation_id)
    : attempt.conversation_id ? attackConversationRoutePath(attempt.attack_result_id, attempt.conversation_id, scenarioResultId)
    : null

  return (
    <Dialog open={attempt !== null} onOpenChange={(_, data) => { if (!data.open) onClose() }}>
      <DialogSurface className={styles.attackDetailDialogSurface}>
        <DialogBody className={styles.attackDetailDialogBody}>
          <DialogTitle action={(
            <Button appearance="subtle" icon={<DismissRegular />} aria-label="Close attack details" onClick={onClose} />
          )}>
            {attempt?.atomic_attack_name || 'Attack attempt'}
          </DialogTitle>
          {attempt && (
            <DialogContent className={styles.attackDetailDialogContent}>
              <AttackAttemptDetails
                attempt={attempt}
                objective={objective}
                atomicGroup={atomicGroupId ? plannedAtomicGroups.get(atomicGroupId) : undefined}
                techniqueDetails={active?.technique_details
                  ?? (atomicGroupId ? progressAtomicGroups.get(atomicGroupId)?.technique_details : null)}
                objectiveScorer={summary.objective_scorer}
                conversationPath={conversationPath}
                conversationState={conversationState}
              />
            </DialogContent>
          )}
          <DialogActions>
            <Button appearance="primary" onClick={onClose}>Close</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
