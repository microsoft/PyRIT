import {
  Badge,
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Text,
  mergeClasses,
} from '@fluentui/react-components'

import type { ScenarioProgressResult, ScenarioRunPlanSeedGroup } from '@/types'

import AttackExecutionTable from './AttackExecutionTable'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

interface ObjectiveDetailsDialogProps {
  readonly objective: ScenarioRunPlanSeedGroup | null
  readonly onClose: () => void
  readonly attempts: ScenarioProgressResult[]
  readonly onOpenAttempt: (attempt: ScenarioProgressResult, trigger: HTMLElement) => void
  readonly scenarioResultId: string
}

export function ObjectiveDetailsDialog({ objective, onClose, attempts, onOpenAttempt, scenarioResultId }: ObjectiveDetailsDialogProps) {
  const styles = useScenarioRunPageStyles()

  return (
    <Dialog
      open={objective !== null}
      onOpenChange={(_, data) => {
        if (!data.open) {
          onClose()
        }
      }}
    >
      <DialogSurface className={styles.attackDetailDialogSurface}>
        <DialogBody className={styles.attackDetailDialogBody}>
          <DialogTitle>Objective</DialogTitle>
          {objective && (
            <DialogContent className={mergeClasses(styles.dialogContent, styles.attackDetailDialogContent)}>
              <Text as="p" className={styles.objective}>{objective.objective}</Text>
              <div>
                <Text as="h3" size={400} weight="semibold">Seed prompt group</Text>
                {objective.prompts.length === 0 ? (
                  <Text className={styles.sectionHint}>This objective has no additional seed prompts.</Text>
                ) : (
                  <div className={styles.seedPromptList}>
                    {objective.prompts.map((prompt, index) => (
                      <article key={`${prompt.sequence}-${index}`} className={styles.seedPrompt}>
                        <div className={styles.badgeList}>
                          <Badge appearance="outline">Sequence {prompt.sequence}</Badge>
                          {prompt.role && <Badge appearance="outline">{prompt.role}</Badge>}
                          {prompt.data_type && <Badge appearance="outline">{prompt.data_type}</Badge>}
                        </div>
                        <Text className={styles.objective}>{prompt.value}</Text>
                      </article>
                    ))}
                  </div>
                )}
              </div>
              <section className={styles.section}>
                <Text as="h3" size={400} weight="semibold">Atomic attacks</Text>
                <AttackExecutionTable attempts={attempts} totalAttempts={attempts.length} showObjective={false}
                  scenarioResultId={scenarioResultId} seedObjectives={new Map([[objective.id, objective.objective]])}
                  onOpenDetails={onOpenAttempt} />
              </section>
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
