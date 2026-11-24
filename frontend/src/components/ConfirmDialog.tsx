import { useLayoutEffect, useRef } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from '@fluentui/react-components'

interface ConfirmDialogProps {
  open: boolean
  title: string
  confirmLabel?: string
  cancelLabel?: string
  onConfirm: () => void
  onCancel: () => void
  children: React.ReactNode
}

export default function ConfirmDialog({
  open,
  title,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
  children,
}: ConfirmDialogProps) {
  // The dialog opens from the open prop and has no trigger, so Fluent has no
  // element to send focus back to. Remember the focused control when the dialog
  // opens and focus it again on close, while it is still on the page.
  const returnFocusTo = useRef<HTMLElement | null>(null)
  useLayoutEffect(() => {
    if (open) {
      returnFocusTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
      return
    }
    const opener = returnFocusTo.current
    returnFocusTo.current = null
    if (opener?.isConnected) {
      opener.focus()
    }
  }, [open])

  return (
    <Dialog open={open} onOpenChange={(_e, data) => { if (!data.open) onCancel() }}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{title}</DialogTitle>
          <DialogContent>{children}</DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={onCancel}>{cancelLabel}</Button>
            <Button appearance="primary" onClick={onConfirm}>{confirmLabel}</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
