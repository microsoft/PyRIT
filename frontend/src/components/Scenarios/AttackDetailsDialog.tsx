import { Button, Dialog, DialogBody, DialogContent, DialogSurface, DialogTitle, Text } from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'

import OperationParentLink from '@/components/Operations/OperationParentLink'
import type { ScenarioRunAttempt } from '@/hooks/useScenarioRunAttackDetails'
import type { ScenarioHierarchyNode, ScenarioProgressHeader } from '@/types'

import ScenarioAttackDetails, { type ScenarioRunDetailsContext } from './ScenarioAttackDetails'
import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

const TITLE_ID = 'attack-details-title'

interface AttackDetailsDialogProps {
  readonly run: ScenarioProgressHeader
  readonly context: ScenarioRunDetailsContext
  readonly attempt: ScenarioRunAttempt | null
  /** The hierarchy node the dialog was opened from, which may carry the objective. */
  readonly hierarchySelection: ScenarioHierarchyNode | null
  readonly onClose: () => void
}

/** One attack's details, opened from anywhere on the run page, under the run's Operation. */
export default function AttackDetailsDialog({ run, context, attempt, hierarchySelection, onClose }: AttackDetailsDialogProps) {
  const styles = useScenarioRunPageStyles()
  return (
    <Dialog open={attempt !== null} onOpenChange={(_, data) => { if (!data.open) onClose() }}>
      <DialogSurface className={styles.attackDetailDialogSurface} aria-labelledby={TITLE_ID}>
        <DialogBody className={styles.attackDetailDialogBody}>
          <DialogTitle as="div" className={styles.attackDetailDialogTitle} action={(
            <Button appearance="subtle" icon={<DismissRegular />} aria-label="Close attack details" onClick={onClose} />
          )}>
            <OperationParentLink id={run.operation_id} name={run.operation_name} />
            <Text as="h2" id={TITLE_ID} size={500} weight="semibold">{attempt?.atomic_attack_name || 'Attack attempt'}</Text>
          </DialogTitle>
          {attempt && (
            <DialogContent className={styles.attackDetailDialogContent}>
              <ScenarioAttackDetails context={context} attempt={attempt} hierarchySelection={hierarchySelection} />
            </DialogContent>
          )}
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
