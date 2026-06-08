import {
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
} from '@fluentui/react-components'
import type { SecretMatch } from './detectSecrets'

interface SecretWarningProps {
  matches: SecretMatch[]
}

/**
 * Inline warning banner shown in the feedback form when `detectSecrets` finds
 * one or more possible credentials in the user's input. Renders nothing when
 * the match list is empty so callers can use it unconditionally.
 *
 * The dialog also has a separate confirm modal that fires on submit; this is
 * the always-visible live warning that nudges the user to redact before they
 * even click submit.
 */
export function SecretWarning({ matches }: SecretWarningProps) {
  if (matches.length === 0) return null
  return (
    <MessageBar intent="warning" data-testid="feedback-secret-warning">
      <MessageBarBody>
        <MessageBarTitle>Possible secret detected</MessageBarTitle>
        Your feedback looks like it may contain:{' '}
        {matches.map((m) => m.label).join(', ')}. Please remove before continuing —
        GitHub issues are public.
      </MessageBarBody>
    </MessageBar>
  )
}
