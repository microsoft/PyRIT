// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Cost-guardrail confirmation modal per spec §8.1. Pure presentational
 * — owns only the "Don't ask again this session" checkbox draft state;
 * commit-on-Refresh, discard-on-Cancel.
 */

import { useState } from 'react'
import {
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
} from '@fluentui/react-components'

import type { WaveTriggerKind } from '../../runner/treeTypes'

export interface CostGuardrailModalProps {
  count: number
  triggerKind: WaveTriggerKind
  threshold: number
  /** Resolves the runner's approve() Promise to true. */
  onRefresh: (commitSuppression: boolean) => void
  /** Resolves to false; suppression draft discarded. */
  onCancel: () => void
}

export function CostGuardrailModal({
  count,
  triggerKind,
  threshold,
  onRefresh,
  onCancel,
}: CostGuardrailModalProps) {
  const [dontAskAgain, setDontAskAgain] = useState(false)
  const bodyClause = bodyClauseFor(triggerKind)
  return (
    <Dialog
      open
      onOpenChange={(_e, data) => {
        if (!data.open) onCancel()
      }}
    >
      <DialogSurface>
        <DialogBody>
          <DialogTitle>{titleFor(triggerKind, count)}</DialogTitle>
          <DialogContent>
            <p>
              {bodyClause} will send {count} call{count === 1 ? '' : 's'} to the target
              (threshold: {threshold} call{threshold === 1 ? '' : 's'} per wave).
            </p>
            <Checkbox
              checked={dontAskAgain}
              onChange={(_e, data) => setDontAskAgain(data.checked === true)}
              label="Don't ask again this session"
            />
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={onCancel}>
              Cancel
            </Button>
            <Button appearance="primary" onClick={() => onRefresh(dontAskAgain)}>
              Refresh
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}

function titleFor(kind: WaveTriggerKind, count: number): string {
  switch (kind) {
    case 'refresh_tree':
      return `Refresh tree (${count} call${count === 1 ? '' : 's'})?`
    case 'refresh_subtree':
      return `Refresh subtree (${count} call${count === 1 ? '' : 's'})?`
    case 'refresh_node':
      return `Refresh node (${count} call${count === 1 ? '' : 's'})?`
    case 'retry_failed':
      return `Retry failed (${count} call${count === 1 ? '' : 's'})?`
    case 'synced_peer_add':
      return `Add synced peer (${count} call${count === 1 ? '' : 's'})?`
    case 'cross_tree_rebase':
      return `Cross-tree rebase (${count} call${count === 1 ? '' : 's'})?`
  }
}

function bodyClauseFor(kind: WaveTriggerKind): string {
  switch (kind) {
    case 'refresh_tree':
      return 'Refreshing the tree'
    case 'refresh_subtree':
      return 'Refreshing this subtree'
    case 'refresh_node':
      return 'Refreshing this node'
    case 'retry_failed':
      return 'Retrying failed nodes'
    case 'synced_peer_add':
      return 'Adding a synced peer'
    case 'cross_tree_rebase':
      return 'Performing a cross-tree rebase'
  }
}
