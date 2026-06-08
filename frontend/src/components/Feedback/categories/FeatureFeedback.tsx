import { Field, Textarea } from '@fluentui/react-components'
import { tooShortMessage, useCategoryStyles } from './shared'

export interface FeatureValues {
  problem: string
  solution: string
  alternatives: string
  additional_context: string
}

interface FeatureFeedbackProps {
  values: FeatureValues
  onChange: (name: keyof FeatureValues, value: string) => void
  primaryTooShort: boolean
}

/** Fields mirroring `.github/ISSUE_TEMPLATE/feature_request.md`. */
export function FeatureFeedback({
  values,
  onChange,
  primaryTooShort,
}: FeatureFeedbackProps) {
  const styles = useCategoryStyles()
  return (
    <>
      <Field label="Is your feature request related to a problem? Please describe.">
        <Textarea
          className={styles.textarea}
          value={values.problem}
          onChange={(_, data) => onChange('problem', data.value)}
          placeholder="I'm always frustrated when…"
          data-testid="feedback-feature-problem-input"
        />
      </Field>
      <Field
        label="Describe the solution you'd like"
        required
        validationMessage={primaryTooShort ? tooShortMessage : undefined}
        validationState={primaryTooShort ? 'warning' : 'none'}
      >
        <Textarea
          className={styles.primaryTextarea}
          value={values.solution}
          onChange={(_, data) => onChange('solution', data.value)}
          data-testid="feedback-feature-solution-input"
        />
      </Field>
      <Field label="Describe alternatives you've considered, if relevant">
        <Textarea
          className={styles.textarea}
          value={values.alternatives}
          onChange={(_, data) => onChange('alternatives', data.value)}
          data-testid="feedback-feature-alternatives-input"
        />
      </Field>
      <Field label="Additional context">
        <Textarea
          className={styles.textarea}
          value={values.additional_context}
          onChange={(_, data) => onChange('additional_context', data.value)}
          data-testid="feedback-feature-context-input"
        />
      </Field>
    </>
  )
}
