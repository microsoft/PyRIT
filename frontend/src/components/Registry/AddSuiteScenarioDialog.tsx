import { type FormEvent, useState } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  MessageBar,
  MessageBarBody,
} from '@fluentui/react-components'

import ScenarioPicker from '@/components/Scenarios/ScenarioPicker'
import type { SuiteMutationOutcome } from '@/hooks/useScenarioSuiteMutations'
import type { RegisteredScenario } from '@/types'

import PartialSaveNotice from './PartialSaveNotice'
import { useSuiteDialogStyles } from './SuiteDialogs.styles'

interface AddSuiteScenarioDialogProps {
  suiteName: string
  scenarios: RegisteredScenario[]
  onAdd: (scenarioName: string, savedPresetName?: string) => Promise<SuiteMutationOutcome>
  onDismiss: () => void
}

/** Adds a scenario, with its scenario's defaults, to the end of a preset. */
export default function AddSuiteScenarioDialog({ suiteName, scenarios, onAdd, onDismiss }: AddSuiteScenarioDialogProps) {
  const styles = useSuiteDialogStyles()
  const [scenarioName, setScenarioName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [savedPresetName, setSavedPresetName] = useState<string>()
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setSaving(true)
    setError(null)
    const outcome = await onAdd(scenarioName, savedPresetName)
    setSaving(false)
    if (outcome.status === 'done') {
      onDismiss()
    } else if (outcome.status === 'partial') {
      setSavedPresetName(outcome.savedPresetName)
      setError(outcome.error)
    } else {
      setError(outcome.error)
    }
  }

  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open && !saving) onDismiss() }}>
      <DialogSurface>
        <form onSubmit={(event) => void handleSubmit(event)} noValidate>
          <DialogBody>
            <DialogTitle>Add a scenario to {suiteName}</DialogTitle>
            <DialogContent className={styles.body}>
              {error && (savedPresetName === undefined ? (
                <MessageBar intent="error">
                  <MessageBarBody>{error}</MessageBarBody>
                </MessageBar>
              ) : (
                <PartialSaveNotice missingFrom={`added to ${suiteName}`} error={error} />
              ))}
              <Field
                label="Scenario"
                required
                hint="You can change its techniques, datasets, and limits after adding it."
              >
                <ScenarioPicker
                  className={styles.control}
                  scenarios={scenarios}
                  value={scenarioName}
                  disabled={saving || savedPresetName !== undefined}
                  onChange={setScenarioName}
                />
              </Field>
            </DialogContent>
            <DialogActions>
              <Button className={styles.touchTarget} appearance="secondary" disabled={saving} onClick={onDismiss}>
                Cancel
              </Button>
              <Button
                className={styles.touchTarget}
                appearance="primary"
                type="submit"
                disabled={saving || scenarioName === ''}
              >
                {saving ? 'Adding...' : savedPresetName === undefined ? 'Add' : 'Retry'}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  )
}
