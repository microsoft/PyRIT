import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  MessageBar,
  MessageBarBody,
  MessageBarTitle,
  Text,
} from '@fluentui/react-components'
import type { SecretMatch } from './detectSecrets'

interface SecretWarningProps {
  matches: SecretMatch[]
  /** Whether the "are you sure?" confirm modal is open. */
  confirmOpen: boolean
  /** Called when the confirm modal wants to change open state (X / outside click / Go back). */
  onConfirmOpenChange: (open: boolean) => void
  /** Called when the user clicks "Submit anyway" in the confirm modal. */
  onConfirmSubmit: () => void
}

/**
 * Two-stage secret warning for the feedback form:
 *
 * 1. An inline `MessageBar` banner shown live while the user types, as soon as
 *    `detectSecrets` finds anything in their input. This is the always-visible
 *    nudge that tells them to redact before submitting.
 * 2. A blocking confirm modal raised by the parent dialog when the user
 *    clicks Submit anyway. The modal lists the matched rule labels and
 *    requires an explicit "Submit anyway" to proceed; the primary action is
 *    the safe "Go back and fix".
 *
 * The component renders nothing when there are no matches AND the confirm
 * modal is not open, so callers can mount it unconditionally.
 */
export function SecretWarning({
  matches,
  confirmOpen,
  onConfirmOpenChange,
  onConfirmSubmit,
}: SecretWarningProps) {
  const hasMatches = matches.length > 0
  if (!hasMatches && !confirmOpen) return null

  return (
    <>
      {hasMatches && (
        <MessageBar intent="warning" data-testid="feedback-secret-warning">
          <MessageBarBody>
            <MessageBarTitle>Possible secret detected</MessageBarTitle>
            Your feedback looks like it may contain:{' '}
            {matches.map((m) => m.label).join(', ')}. Please remove before continuing —
            GitHub issues are public.
          </MessageBarBody>
        </MessageBar>
      )}

      {hasMatches && (
        <Dialog
          open={confirmOpen}
          onOpenChange={(_, data) => onConfirmOpenChange(data.open)}
        >
          <DialogSurface data-testid="feedback-confirm-dialog">
            <DialogBody>
              <DialogTitle>Possible secret in your feedback</DialogTitle>
              <DialogContent>
                <Text>
                  Your feedback looks like it may contain:{' '}
                  <strong>{matches.map((m) => m.label).join(', ')}</strong>.
                </Text>
                <br />
                <Text>
                  The GitHub issue at <strong>github.com/microsoft/PyRIT</strong> is public —
                  anyone can read it. Are you sure you want to continue?
                </Text>
              </DialogContent>
              <DialogActions>
                <Button
                  appearance="primary"
                  onClick={() => onConfirmOpenChange(false)}
                  data-testid="feedback-confirm-cancel"
                >
                  Go back and fix
                </Button>
                <Button
                  appearance="secondary"
                  onClick={onConfirmSubmit}
                  data-testid="feedback-confirm-submit"
                >
                  Submit anyway
                </Button>
              </DialogActions>
            </DialogBody>
          </DialogSurface>
        </Dialog>
      )}
    </>
  )
}
