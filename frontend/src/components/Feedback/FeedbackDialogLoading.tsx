import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  Spinner,
} from '@fluentui/react-components'

import { useFeedbackDialogLoadingStyles } from './FeedbackDialogLoading.styles'

interface FeedbackDialogLoadingProps {
  onClose: () => void
}

/** Shown while the Feedback form loads on first open, so the click is acknowledged and can be cancelled. */
export default function FeedbackDialogLoading({ onClose }: FeedbackDialogLoadingProps) {
  const styles = useFeedbackDialogLoadingStyles()
  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open) onClose() }}>
      <DialogSurface aria-label="Loading feedback">
        <DialogBody>
          <DialogContent>
            <Spinner label="Loading feedback..." />
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" className={styles.cancelButton} onClick={onClose}>
              Cancel
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
