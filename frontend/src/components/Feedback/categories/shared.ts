import { makeStyles, tokens } from '@fluentui/react-components'

/**
 * Minimum length we require on each category's primary (required) field
 * before the dialog will let the user submit. Kept in this shared module so
 * both the dialog (validation gate) and each `<*Feedback>` component (inline
 * `validationMessage`) read from one source of truth.
 */
export const MIN_PRIMARY_LENGTH = 10

export const tooShortMessage = `Please provide at least ${MIN_PRIMARY_LENGTH} characters.`

/**
 * Styles shared by every `<*Feedback>` category component so each one stays
 * presentational and self-contained.
 */
export const useCategoryStyles = makeStyles({
  textarea: {
    minHeight: '90px',
  },
  primaryTextarea: {
    minHeight: '140px',
  },
  helper: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
  },
})
