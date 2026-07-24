import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from '@fluentui/react-components'

import InitializerConfig from './InitializerConfig'
import { useInitializerSettingsDialogStyles } from './InitializerSettingsDialog.styles'

interface InitializerSettingsDialogProps {
  open: boolean
  onClose: () => void
}

export default function InitializerSettingsDialog({ open, onClose }: InitializerSettingsDialogProps) {
  const styles = useInitializerSettingsDialogStyles()

  return (
    <Dialog open={open} onOpenChange={(_, data) => { if (!data.open) onClose() }}>
      <DialogSurface className={styles.dialogSurface}>
        <DialogBody>
          <DialogTitle>Initializer Settings</DialogTitle>
          <DialogContent className={styles.dialogContent}>
            <InitializerConfig />
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={onClose}>
              Close
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
