import { Field, Textarea } from '@fluentui/react-components'
import { tooShortMessage, useCategoryStyles } from './shared'

export interface PraiseValues {
  body: string
}

interface PraiseFeedbackProps {
  values: PraiseValues
  onChange: (name: keyof PraiseValues, value: string) => void
  primaryTooShort: boolean
}

/** Single-field component mirroring `.github/ISSUE_TEMPLATE/praise.md`. */
export function PraiseFeedback({
  values,
  onChange,
  primaryTooShort,
}: PraiseFeedbackProps) {
  const styles = useCategoryStyles()
  return (
    <Field
      label="What do you love?"
      required
      validationMessage={primaryTooShort ? tooShortMessage : undefined}
      validationState={primaryTooShort ? 'warning' : 'none'}
    >
      <Textarea
        className={styles.primaryTextarea}
        value={values.body}
        onChange={(_, data) => onChange('body', data.value)}
        placeholder="Tell us what's working well"
        data-testid="feedback-praise-body-input"
      />
    </Field>
  )
}
