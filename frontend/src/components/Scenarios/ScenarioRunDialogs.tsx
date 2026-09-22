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
} from '@fluentui/react-components'

import type { ScenarioRunPlanSeedGroup, ScenarioTechniqueProgress } from '@/types'

import { useScenarioRunPageStyles } from './ScenarioRunPage.styles'

interface TechniqueDetailsDialogProps {
  readonly technique: ScenarioTechniqueProgress | null
  readonly onClose: () => void
}

export function TechniqueDetailsDialog({ technique, onClose }: TechniqueDetailsDialogProps) {
  const styles = useScenarioRunPageStyles()

  return (
    <Dialog
      open={technique !== null}
      onOpenChange={(_, data) => {
        if (!data.open) {
          onClose()
        }
      }}
    >
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{technique?.display_group ?? 'Technique details'}</DialogTitle>
          {technique && (
            <DialogContent className={styles.dialogContent}>
              <div>
                <Text size={200} className={styles.metadataLabel}>Description</Text>
                <Text as="p" className={styles.objective}>
                  {technique.description ?? 'No description is available for this technique.'}
                </Text>
              </div>
              <div>
                <Text size={200} className={styles.metadataLabel}>Atomic attacks</Text>
                <div className={styles.detailList}>
                  {technique.atomic_attack_names.map((attackName) => (
                    <Text key={attackName}>{attackName}</Text>
                  ))}
                </div>
              </div>
              {technique.tags.length > 0 && (
                <div>
                  <Text size={200} className={styles.metadataLabel}>Tags</Text>
                  <div className={styles.badgeList}>
                    {technique.tags.map((tag) => (
                      <Badge key={tag} appearance="outline">{tag}</Badge>
                    ))}
                  </div>
                </div>
              )}
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

interface ObjectiveDetailsDialogProps {
  readonly objective: ScenarioRunPlanSeedGroup | null
  readonly onClose: () => void
}

export function ObjectiveDetailsDialog({ objective, onClose }: ObjectiveDetailsDialogProps) {
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
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Objective</DialogTitle>
          {objective && (
            <DialogContent className={styles.dialogContent}>
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
