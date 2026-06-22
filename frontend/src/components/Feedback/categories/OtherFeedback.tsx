import { Field, Textarea } from '@fluentui/react-components'
import { tooShortMessage, useCategoryStyles } from './shared'

export interface OtherValues {
  body: string
}

interface OtherFeedbackProps {
  values: OtherValues
  onChange: (name: keyof OtherValues, value: string) => void
  primaryTooShort: boolean
}

/** Catch-all single-field component for the `blank_template.md` path. */
export function OtherFeedback({
  values,
  onChange,
  primaryTooShort,
}: OtherFeedbackProps) {
  const styles = useCategoryStyles()
  return (
    <Field
      label="What would you like us to know?"
      required
      validationMessage={primaryTooShort ? tooShortMessage : undefined}
      validationState={primaryTooShort ? 'warning' : 'none'}
    >
      <Textarea
        className={styles.primaryTextarea}
        value={values.body}
        onChange={(_, data) => onChange('body', data.value)}
        data-testid="feedback-other-body-input"
      />
    </Field>
  )
}
