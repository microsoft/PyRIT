import { useMemo, useState } from 'react'
import {
  Dialog,
  DialogSurface,
  DialogTitle,
  DialogBody,
  DialogContent,
  DialogActions,
  Button,
  Field,
  Input,
  Link,
  Select,
  Text,
  tokens,
  makeStyles,
} from '@fluentui/react-components'
import { OpenRegular } from '@fluentui/react-icons'
import {
  buildGithubFeedbackUrl,
  getCategoryLabel,
  type FeedbackCategory,
  type FeedbackContext,
  type FeedbackInput,
} from './feedbackUrl'
import { detectSecrets } from './detectSecrets'
import { SecretWarning } from './SecretWarning'
import {
  BugFeedback,
  DocFeedback,
  FeatureFeedback,
  MIN_PRIMARY_LENGTH,
  OtherFeedback,
  PraiseFeedback,
} from './categories'

interface FeedbackDialogProps {
  open: boolean
  onClose: () => void
  context?: FeedbackContext
}

// The order here is also the order in the dropdown.
const CATEGORIES: { value: FeedbackCategory; helper: string }[] = [
  { value: 'bug', helper: 'Something is broken or producing the wrong result' },
  { value: 'feature', helper: 'An idea or improvement you would like to see' },
  { value: 'doc', helper: 'Documentation is missing, confusing, or out of date' },
  { value: 'praise', helper: 'Something you love about Co-PyRIT — auto-acknowledged' },
  { value: 'other', helper: 'Anything else' },
]

// Keep the assembled body short enough that the URL-encoded GitHub issue URL
// fits well within browser and intermediate-proxy limits (~8 KB URL is safe).
const MAX_FIELD_LENGTH = 5_000
const MAX_CONTACT_LENGTH = 200

interface DialogFields {
  // bug
  describe?: string
  repro?: string
  expected?: string
  actual?: string
  versions?: string
  // feature
  problem?: string
  solution?: string
  alternatives?: string
  additional_context?: string
  // doc
  issue?: string
  suggestion?: string
  // praise / other
  body?: string
}

const useStyles = makeStyles({
  form: {
    display: 'flex',
    flexDirection: 'column',
    rowGap: tokens.spacingVerticalM,
    paddingTop: tokens.spacingVerticalS,
  },
  warning: {
    color: tokens.colorPaletteDarkOrangeForeground1,
    fontWeight: tokens.fontWeightSemibold,
  },
  helper: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
  },
  categoryHelper: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
    marginTop: tokens.spacingVerticalXS,
  },
})

/** Returns true iff the user has filled in enough to build a useful issue. */
function getPrimaryField(
  category: FeedbackCategory,
  fields: DialogFields,
): { name: keyof DialogFields; value: string } {
  switch (category) {
    case 'bug':
      return { name: 'describe', value: fields.describe ?? '' }
    case 'feature':
      return { name: 'solution', value: fields.solution ?? '' }
    case 'doc':
      return { name: 'issue', value: fields.issue ?? '' }
    case 'praise':
    case 'other':
      return { name: 'body', value: fields.body ?? '' }
  }
}

function buildInput(
  category: FeedbackCategory,
  fields: DialogFields,
  optional_contact: string | undefined,
  context: FeedbackContext | undefined,
): FeedbackInput {
  const clean = (s: string | undefined) => (s && s.trim().length > 0 ? s.trim() : undefined)
  const common = {
    optional_contact: clean(optional_contact),
    context,
  }
  switch (category) {
    case 'bug':
      return {
        category: 'bug',
        describe: (fields.describe ?? '').trim(),
        repro: clean(fields.repro),
        expected: clean(fields.expected),
        actual: clean(fields.actual),
        versions: clean(fields.versions),
        ...common,
      }
    case 'feature':
      return {
        category: 'feature',
        problem: clean(fields.problem),
        solution: (fields.solution ?? '').trim(),
        alternatives: clean(fields.alternatives),
        additional_context: clean(fields.additional_context),
        ...common,
      }
    case 'doc':
      return {
        category: 'doc',
        issue: (fields.issue ?? '').trim(),
        suggestion: clean(fields.suggestion),
        ...common,
      }
    case 'praise':
      return { category: 'praise', body: (fields.body ?? '').trim(), ...common }
    case 'other':
      return { category: 'other', body: (fields.body ?? '').trim(), ...common }
  }
}

export default function FeedbackDialog({ open, onClose, context }: FeedbackDialogProps) {
  const styles = useStyles()
  const [category, setCategory] = useState<FeedbackCategory>('bug')
  const [fields, setFields] = useState<DialogFields>({})
  const [optionalContact, setOptionalContact] = useState('')
  const [confirmOpen, setConfirmOpen] = useState(false)

  const update = (name: keyof DialogFields, value: string) =>
    setFields((prev) => ({ ...prev, [name]: value }))

  const primary = getPrimaryField(category, fields)
  const primaryTrimmed = primary.value.trim()
  const primaryTooShort =
    primaryTrimmed.length > 0 && primaryTrimmed.length < MIN_PRIMARY_LENGTH

  const fieldTooLong = Object.values(fields).some(
    (v) => typeof v === 'string' && v.length > MAX_FIELD_LENGTH,
  )

  const canSubmit = useMemo(
    () =>
      primaryTrimmed.length >= MIN_PRIMARY_LENGTH &&
      !fieldTooLong &&
      optionalContact.length <= MAX_CONTACT_LENGTH,
    [primaryTrimmed, fieldTooLong, optionalContact],
  )

  // Run secret detection across every text field plus the contact field.
  const secretMatches = useMemo(() => {
    const blob = [
      fields.describe,
      fields.repro,
      fields.expected,
      fields.actual,
      fields.versions,
      fields.problem,
      fields.solution,
      fields.alternatives,
      fields.additional_context,
      fields.issue,
      fields.suggestion,
      fields.body,
      optionalContact,
    ]
      .filter(Boolean)
      .join('\n')
    return detectSecrets(blob)
  }, [fields, optionalContact])

  const handleSubmit = () => {
    if (!canSubmit) return
    if (secretMatches.length > 0) {
      setConfirmOpen(true)
      return
    }
    fireSubmit()
  }

  const fireSubmit = () => {
    const input = buildInput(category, fields, optionalContact || undefined, context)
    const url = buildGithubFeedbackUrl(input)
    window.open(url, '_blank', 'noopener,noreferrer')
    setConfirmOpen(false)
    onClose()
  }

  const helperForCategory =
    CATEGORIES.find((c) => c.value === category)?.helper ?? ''

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(_, data) => {
          if (!data.open) onClose()
        }}
      >
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Send feedback</DialogTitle>
            <DialogContent>
              <form
                className={styles.form}
                onSubmit={(e) => {
                  e.preventDefault()
                  handleSubmit()
                }}
              >
                <Text className={styles.warning} data-testid="feedback-sensitive-warning">
                  GitHub issues are public. Please do not include secrets, credentials,
                  customer data, model endpoints, or other proprietary information. Your
                  feedback will be filed at{' '}
                  <Link
                    href="https://github.com/microsoft/PyRIT/issues"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    github.com/microsoft/PyRIT
                  </Link>
                  .
                </Text>

                <Field label="Category" required>
                  <Select
                    value={category}
                    onChange={(_, data) => {
                      setCategory(data.value as FeedbackCategory)
                      // Keep contact field; clear the rest so old answers don't
                      // accidentally end up under a different template.
                      setFields({})
                    }}
                    data-testid="feedback-category-select"
                  >
                    {CATEGORIES.map((c) => (
                      <option key={c.value} value={c.value}>
                        {getCategoryLabel(c.value)}
                      </option>
                    ))}
                  </Select>
                  <Text className={styles.categoryHelper}>{helperForCategory}</Text>
                </Field>

                <CategoryRenderer
                  category={category}
                  fields={fields}
                  update={update}
                  primaryTooShort={primaryTooShort}
                />

                <Field label="Preferred contact (optional)">
                  <Input
                    value={optionalContact}
                    onChange={(_, data) => setOptionalContact(data.value)}
                    placeholder="GitHub handle, email, alias — if you would like a reply"
                    data-testid="feedback-contact-input"
                  />
                </Field>

                <SecretWarning
                  matches={secretMatches}
                  confirmOpen={confirmOpen}
                  onConfirmOpenChange={setConfirmOpen}
                  onConfirmSubmit={fireSubmit}
                />

                <Text className={styles.helper}>
                  Continuing opens a new tab on github.com with this form pre-filled. You
                  will need a GitHub account to file the issue. Data you submit is
                  governed by the{' '}
                  <Link
                    href="https://privacy.microsoft.com/en-us/privacystatement"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Microsoft Privacy Statement
                  </Link>
                  .
                </Text>
              </form>
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={onClose}>
                Cancel
              </Button>
              <Button
                appearance="primary"
                onClick={handleSubmit}
                disabled={!canSubmit}
                icon={<OpenRegular />}
                iconPosition="after"
                data-testid="feedback-submit-button"
              >
                Continue on GitHub
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </>
  )
}


interface CategoryRendererProps {
  category: FeedbackCategory
  fields: DialogFields
  update: (name: keyof DialogFields, value: string) => void
  primaryTooShort: boolean
}

/**
 * Picks the right `<*Feedback>` component for the active category and feeds
 * it a typed slice of the dialog's flat field state. The components in
 * `./categories/` are presentational; this dispatcher is the only place that
 * knows about all of them.
 */
function CategoryRenderer({
  category,
  fields,
  update,
  primaryTooShort,
}: CategoryRendererProps) {
  switch (category) {
    case 'bug':
      return (
        <BugFeedback
          values={{
            describe: fields.describe ?? '',
            repro: fields.repro ?? '',
            expected: fields.expected ?? '',
            actual: fields.actual ?? '',
            versions: fields.versions ?? '',
          }}
          onChange={update}
          primaryTooShort={primaryTooShort}
        />
      )
    case 'feature':
      return (
        <FeatureFeedback
          values={{
            problem: fields.problem ?? '',
            solution: fields.solution ?? '',
            alternatives: fields.alternatives ?? '',
            additional_context: fields.additional_context ?? '',
          }}
          onChange={update}
          primaryTooShort={primaryTooShort}
        />
      )
    case 'doc':
      return (
        <DocFeedback
          values={{
            issue: fields.issue ?? '',
            suggestion: fields.suggestion ?? '',
          }}
          onChange={update}
          primaryTooShort={primaryTooShort}
        />
      )
    case 'praise':
      return (
        <PraiseFeedback
          values={{ body: fields.body ?? '' }}
          onChange={update}
          primaryTooShort={primaryTooShort}
        />
      )
    case 'other':
      return (
        <OtherFeedback
          values={{ body: fields.body ?? '' }}
          onChange={update}
          primaryTooShort={primaryTooShort}
        />
      )
  }
}
