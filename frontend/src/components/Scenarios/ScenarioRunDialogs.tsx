import {
  Badge,
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  MessageBar,
  MessageBarBody,
  Text,
  mergeClasses,
} from '@fluentui/react-components'

import type { ScenarioProgressResult, ScenarioRunPlanSeedGroup } from '@/types'
import type { UseScenarioResultHierarchyResult } from '@/hooks/useScenarioResultHierarchy'

import AttackExecutionTable from './AttackExecutionTable'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

const EMPTY_HIERARCHY: UseScenarioResultHierarchyResult = {
  roots: {
    status: 'loading',
    runStatus: null,
    groups: [],
    overallCounts: null,
    diagnostics: null,
    truncated: false,
    nextCursor: null,
    loadingMore: false,
    error: null,
    staleCursor: false,
  },
  children: new Map(),
  expanded: new Set(),
  toggleNode: () => {},
  reloadRoots: () => {},
  loadMoreRoots: () => {},
  openChildren: () => {},
  loadMoreChildren: () => {},
  restartChildren: () => {},
}

interface ObjectiveDetailsDialogProps {
  readonly objective: ScenarioRunPlanSeedGroup | null
  readonly onClose: () => void
  readonly attempts: ScenarioProgressResult[]
  readonly onOpenAttempt: (attempt: ScenarioProgressResult, trigger: HTMLElement) => void
  readonly scenarioResultId: string
  /** Technique shorthand per atomic group, shown under each attack. */
  readonly techniqueNames: ReadonlyMap<string, string>
}

/** An objective's text, its seed prompts, and the attacks that pursued it. */
export function ObjectiveDetailsDialog({
  objective, onClose, attempts, onOpenAttempt, scenarioResultId, techniqueNames,
}: ObjectiveDetailsDialogProps) {
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
                <AttackExecutionTable label="Atomic attacks" attempts={attempts} totalAttempts={attempts.length}
                  roots={[]} hierarchy={EMPTY_HIERARCHY}
                  scenarioResultId={scenarioResultId} seedObjectives={new Map([[objective.id, objective.objective]])}
                  techniqueNames={techniqueNames} onOpenDetails={onOpenAttempt} showTimestamp={false} booleanOutcome />
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

interface CancelScanDialogProps {
  readonly open: boolean
  readonly cancelling: boolean
  readonly error: string | null
  readonly onOpenChange: (open: boolean) => void
  readonly onConfirm: () => void
}

export function CancelScanDialog({ open, cancelling, error, onOpenChange, onConfirm }: CancelScanDialogProps) {
  const styles = useScenarioRunPageStyles()
  return (
    <Dialog open={open} onOpenChange={(_, data) => { onOpenChange(data.open) }}>
      <DialogSurface><DialogBody><DialogTitle>Cancel scan?</DialogTitle><DialogContent className={styles.dialogContent}>
        <Text>Stop remaining attacks. Saved conversations and results stay available.</Text>
        {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      </DialogContent><DialogActions><Button disabled={cancelling} onClick={() => { onOpenChange(false) }}>Keep running</Button>
        <Button appearance="primary" disabled={cancelling} onClick={onConfirm}>{cancelling ? 'Cancelling...' : 'Cancel scan'}</Button>
      </DialogActions></DialogBody></DialogSurface>
    </Dialog>
  )
}
