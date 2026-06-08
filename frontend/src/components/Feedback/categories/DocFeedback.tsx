import { Field, Textarea } from '@fluentui/react-components'
import { tooShortMessage, useCategoryStyles } from './shared'

export interface DocValues {
  issue: string
  suggestion: string
}

interface DocFeedbackProps {
  values: DocValues
  onChange: (name: keyof DocValues, value: string) => void
  primaryTooShort: boolean
}

/** Fields mirroring `.github/ISSUE_TEMPLATE/doc_improvement.md`. */
export function DocFeedback({ values, onChange, primaryTooShort }: DocFeedbackProps) {
  const styles = useCategoryStyles()
  return (
    <>
      <Field
        label="Describe the issue linked to the documentation"
        required
        validationMessage={primaryTooShort ? tooShortMessage : undefined}
        validationState={primaryTooShort ? 'warning' : 'none'}
      >
        <Textarea
          className={styles.primaryTextarea}
          value={values.issue}
          onChange={(_, data) => onChange('issue', data.value)}
          placeholder="What is confusing or missing?"
          data-testid="feedback-doc-issue-input"
        />
      </Field>
      <Field label="Suggest a potential alternative/fix">
        <Textarea
          className={styles.textarea}
          value={values.suggestion}
          onChange={(_, data) => onChange('suggestion', data.value)}
          data-testid="feedback-doc-suggestion-input"
        />
      </Field>
    </>
  )
}
