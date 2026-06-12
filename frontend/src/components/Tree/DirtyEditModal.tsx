// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Dirty-edit confirmation modal per spec 01 §13.1a. Pure presentational
 * — shown when an in-app tree swap (Switch tree / new / close) is
 * attempted while the current tree carries unrefreshed edits. The
 * operator either discards the edits and continues, or cancels and
 * stays to Refresh first.
 */

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from '@fluentui/react-components'

export interface DirtyEditModalProps {
  /** Count of unrefreshed (`edited` / `draft`) nodes in the current tree. */
  count: number
  /** Operator confirmed discard — run the deferred swap. */
  onDiscard: () => void
  /** Operator cancelled — keep the current tree, abandon the swap. */
  onCancel: () => void
}

export function DirtyEditModal({ count, onDiscard, onCancel }: DirtyEditModalProps) {
  return (
    <Dialog
      open
      onOpenChange={(_e, data) => {
        if (!data.open) onCancel()
      }}
    >
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Discard unsaved edits?</DialogTitle>
          <DialogContent>
            <p>
              You have {count} unsaved edit{count === 1 ? '' : 's'} that will be lost when
              switching trees. Refresh the tree first to persist them as AttackResults, or
              continue to discard.
            </p>
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={onCancel}>
              Cancel
            </Button>
            <Button appearance="primary" onClick={onDiscard}>
              Discard and continue
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
