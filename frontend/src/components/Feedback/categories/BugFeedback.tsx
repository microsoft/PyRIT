import { Field, Text, Textarea } from '@fluentui/react-components'
import { tooShortMessage, useCategoryStyles } from './shared'

export interface BugValues {
  describe: string
  repro: string
  expected: string
  actual: string
  versions: string
}

interface BugFeedbackProps {
  values: BugValues
  onChange: (name: keyof BugValues, value: string) => void
  primaryTooShort: boolean
}

/** Fields mirroring `.github/ISSUE_TEMPLATE/bug_report.md`. */
export function BugFeedback({ values, onChange, primaryTooShort }: BugFeedbackProps) {
  const styles = useCategoryStyles()
  return (
    <>
      <Field
        label="Describe the bug"
        required
        validationMessage={primaryTooShort ? tooShortMessage : undefined}
        validationState={primaryTooShort ? 'warning' : 'none'}
      >
        <Textarea
          className={styles.primaryTextarea}
          value={values.describe}
          onChange={(_, data) => onChange('describe', data.value)}
          data-testid="feedback-bug-describe-input"
        />
      </Field>
      <Field label="Steps/Code to Reproduce">
        <Textarea
          className={styles.textarea}
          value={values.repro}
          onChange={(_, data) => onChange('repro', data.value)}
          placeholder="Minimal example or numbered steps"
          data-testid="feedback-bug-repro-input"
        />
      </Field>
      <Field label="Expected Results">
        <Textarea
          className={styles.textarea}
          value={values.expected}
          onChange={(_, data) => onChange('expected', data.value)}
          data-testid="feedback-bug-expected-input"
        />
      </Field>
      <Field label="Actual Results">
        <Textarea
          className={styles.textarea}
          value={values.actual}
          onChange={(_, data) => onChange('actual', data.value)}
          data-testid="feedback-bug-actual-input"
        />
      </Field>
      <Field label="Versions">
        <Textarea
          className={styles.textarea}
          value={values.versions}
          onChange={(_, data) => onChange('versions', data.value)}
          placeholder="OS, Python version, PyRIT version"
          data-testid="feedback-bug-versions-input"
        />
      </Field>
      <Text className={styles.helper}>
        Have a screenshot? Drag it into the issue body after you continue on GitHub.
      </Text>
    </>
  )
}
