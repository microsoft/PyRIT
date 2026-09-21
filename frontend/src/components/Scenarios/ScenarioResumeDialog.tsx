import { useState } from 'react'

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
  Text,
  type DialogOpenChangeData,
  type DialogOpenChangeEvent,
  type SpinButtonChangeEvent,
  type SpinButtonOnChangeData,
} from '@fluentui/react-components'

import SingleStepSpinButton from '@/components/Parameters/SingleStepSpinButton'
import type { ScenarioResumeExecutionOptions } from '@/types'

import { useScenarioResumeDialogStyles } from './ScenarioResumeDialog.styles'

const MIN_CONCURRENCY = 1
const MAX_CONCURRENCY = 100
const MIN_RETRIES = 0
const MAX_RETRIES = 20

interface ScenarioResumeDialogProps {
  readonly scenarioResultId: string
  readonly pending: boolean
  readonly error: string | null
  readonly onCancel: () => void
  readonly onConfirm: (options: ScenarioResumeExecutionOptions) => void
}

function numericValue(data: SpinButtonOnChangeData): number | null {
  if (typeof data.value === 'number') return data.value
  return data.displayValue?.trim() ? Number(data.displayValue) : null
}

export default function ScenarioResumeDialog({
  scenarioResultId,
  pending,
  error,
  onCancel,
  onConfirm,
}: ScenarioResumeDialogProps) {
  const styles = useScenarioResumeDialogStyles()
  const [concurrency, setConcurrency] = useState<number | null>(MIN_CONCURRENCY)
  const [retries, setRetries] = useState<number | null>(MIN_RETRIES)
  const validConcurrency = concurrency !== null && Number.isInteger(concurrency)
    && concurrency >= MIN_CONCURRENCY && concurrency <= MAX_CONCURRENCY
  const validRetries = retries !== null && Number.isInteger(retries)
    && retries >= MIN_RETRIES && retries <= MAX_RETRIES

  return (
    <Dialog
      open
      onOpenChange={(_event: DialogOpenChangeEvent, data: DialogOpenChangeData) => {
        if (!data.open && !pending) onCancel()
      }}
    >
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Resume run with execution options</DialogTitle>
          <DialogContent className={styles.content}>
            <Text>
              This older run did not save its concurrency and retry limits. Choose these limits
              and confirm Resume to continue unfinished work.
            </Text>
            <Text>
              The original target, scenario, techniques, datasets, parameters, and labels are
              restored by the server. Finished results and the run ID are preserved.
            </Text>
            <Text className={styles.runId}>Run ID: <code>{scenarioResultId}</code></Text>
            {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
            <Field
              label="Max concurrency"
              validationState={validConcurrency ? 'none' : 'error'}
              validationMessage={validConcurrency ? undefined : 'Enter a whole number from 1 to 100.'}
            >
              <SingleStepSpinButton
                className={styles.numberInput}
                value={concurrency}
                min={MIN_CONCURRENCY}
                max={MAX_CONCURRENCY}
                step={1}
                disabled={pending}
                onChange={(_event: SpinButtonChangeEvent, data: SpinButtonOnChangeData) => {
                  setConcurrency(numericValue(data))
                }}
              />
            </Field>
            <Field
              label="Max retries"
              validationState={validRetries ? 'none' : 'error'}
              validationMessage={validRetries ? undefined : 'Enter a whole number from 0 to 20.'}
            >
              <SingleStepSpinButton
                className={styles.numberInput}
                value={retries}
                min={MIN_RETRIES}
                max={MAX_RETRIES}
                step={1}
                disabled={pending}
                onChange={(_event: SpinButtonChangeEvent, data: SpinButtonOnChangeData) => {
                  setRetries(numericValue(data))
                }}
              />
            </Field>
          </DialogContent>
          <DialogActions>
            <Button className={styles.button} disabled={pending} onClick={onCancel}>Cancel</Button>
            <Button
              className={styles.button}
              appearance="primary"
              disabled={pending || !validConcurrency || !validRetries}
              onClick={() => {
                if (validConcurrency && validRetries) {
                  onConfirm({ max_concurrency: concurrency, max_retries: retries })
                }
              }}
            >
              {pending ? 'Resuming...' : 'Resume'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
