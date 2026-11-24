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
  Input,
  MessageBar,
  MessageBarBody,
  Textarea,
} from '@fluentui/react-components'

import ScenarioPicker from '@/components/Scenarios/ScenarioPicker'
import type { NewSuiteInput, SuiteMutationOutcome } from '@/hooks/useScenarioSuiteMutations'
import type { RegisteredScenario } from '@/types'
import { validateRegistryName } from '@/utils/registryName'

import PartialSaveNotice from './PartialSaveNotice'
import { useSuiteDialogStyles } from './SuiteDialogs.styles'

const SCENARIO_HINT = 'You can change its techniques, datasets, and limits after adding it.'

interface NewSuiteDialogProps {
  scenarios: RegisteredScenario[]
  onCreate: (input: NewSuiteInput, savedPresetName?: string) => Promise<SuiteMutationOutcome>
  onCreated: (name: string) => void
  onDismiss: () => void
}

/** Creates a preset with its first scenario; a preset is never created empty. */
export default function NewSuiteDialog({ scenarios, onCreate, onCreated, onDismiss }: NewSuiteDialogProps) {
  const styles = useSuiteDialogStyles()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [scenarioName, setScenarioName] = useState('')
  const [nameError, setNameError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [savedPresetName, setSavedPresetName] = useState<string>()
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    const trimmedName = name.trim()
    const validation = validateRegistryName(trimmedName)
    if (validation) {
      setNameError(validation)
      return
    }
    setSaving(true)
    setError(null)
    const outcome = await onCreate({ name: trimmedName, description, scenarioName }, savedPresetName)
    setSaving(false)
    if (outcome.status === 'done') {
      onCreated(trimmedName)
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
            <DialogTitle>New preset</DialogTitle>
            <DialogContent className={styles.body}>
              {error && (savedPresetName === undefined ? (
                <MessageBar intent="error">
                  <MessageBarBody>{error}</MessageBarBody>
                </MessageBar>
              ) : (
                <PartialSaveNotice missingFrom="added to a new preset" error={error} />
              ))}
              <Field
                label="Name"
                required
                hint="Lowercase letters, digits, and underscores, starting with a letter."
                validationState={nameError ? 'error' : 'none'}
                validationMessage={nameError ?? undefined}
              >
                <Input
                  className={styles.control}
                  value={name}
                  placeholder="e.g. release_candidate_gate"
                  disabled={saving}
                  onChange={(_, data) => {
                    setName(data.value)
                    setNameError(null)
                  }}
                />
              </Field>
              <Field label="Description">
                <Textarea
                  className={styles.control}
                  value={description}
                  disabled={saving}
                  onChange={(_, data) => setDescription(data.value)}
                />
              </Field>
              <Field label="Scenario" required hint={SCENARIO_HINT}>
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
                disabled={saving || name.trim() === '' || scenarioName === ''}
              >
                {saving ? 'Creating...' : savedPresetName === undefined ? 'Create' : 'Retry'}
              </Button>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  )
}
