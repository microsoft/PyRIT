// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import type { NodeProps } from '@xyflow/react'
import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Input,
  Tooltip,
} from '@fluentui/react-components'
import { AddRegular, BranchForkRegular, FlashRegular } from '@fluentui/react-icons'
import { useState } from 'react'

import type { SendNode } from '../../runner/treeTypes'
import type { TreeFlowNode } from './conversationTreeToReactFlow'
import { useActionCallbacks } from './actionCallbacksContext'
import { CardBody, CardFrame, MetaRow } from './cardFrame'
import { useNodeCardStyles } from './nodeCards.styles'

type SendProps = NodeProps<Extract<TreeFlowNode, { type: 'send' }>>

export function SendCard({ data, selected }: SendProps) {
  const node: SendNode = data.node
  const styles = useNodeCardStyles()
  const callbacks = useActionCallbacks()
  const [attemptDialogOpen, setAttemptDialogOpen] = useState(false)
  const [attemptCountDraft, setAttemptCountDraft] = useState('5')
  const hasResponsePreview = node.params.responsePreview !== undefined && node.params.responsePreview.length > 0
  const kindLabel = hasResponsePreview ? 'Assistant response' : 'Pending response'
  const parsedAttemptCount = Number(attemptCountDraft)
  const attemptCountValid = Number.isInteger(parsedAttemptCount) && parsedAttemptCount >= 2 && parsedAttemptCount <= 50
  const createAttemptFan = () => {
    if (!attemptCountValid) return
    callbacks?.onCreateFanFromNode?.(node.id, 'attempt', { attemptCount: parsedAttemptCount })
    setAttemptDialogOpen(false)
  }
  const kindActions = callbacks !== null ? (
    <>
      {callbacks.onAppendChild !== undefined && (
        <Tooltip content="Add follow-up prompt" relationship="description">
          <Button
            size="small"
            appearance="subtle"
            icon={<AddRegular />}
            aria-label="Add follow-up prompt"
            onClick={() => callbacks.onAppendChild?.(node.id, 'follow_up_user_turn')}
          />
        </Tooltip>
      )}
      {callbacks.onCreateFanFromNode !== undefined && (
        <>
          <Tooltip content="Fan out response attempts" relationship="description">
            <Button
              size="small"
              appearance="subtle"
              icon={<BranchForkRegular />}
              aria-label="Fan out response attempts"
              onClick={() => setAttemptDialogOpen(true)}
            />
          </Tooltip>
          <Tooltip content="Compare converters" relationship="description">
            <Button
              size="small"
              appearance="subtle"
              icon={<FlashRegular />}
              aria-label="Compare converters"
              onClick={() => callbacks.onCreateFanFromNode?.(node.id, 'converter')}
            />
          </Tooltip>
        </>
      )}
    </>
  ) : undefined
  return (
    <CardFrame
      kindLabel={kindLabel}
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
      fanChildInfo={data.fanChildInfo}
      kindActions={kindActions}
    >
      {hasResponsePreview && (
        <CardBody text={node.params.responsePreview ?? ''} />
      )}
      {node.params.targetRegistryName !== undefined && (
        <MetaRow label="target" value={node.params.targetRegistryName} />
      )}
      {node.state === 'failed' && node.lastError !== null && (
        <div className={styles.errorPanel}>{node.lastError.message}</div>
      )}
      <Dialog open={attemptDialogOpen} onOpenChange={(_e, d) => setAttemptDialogOpen(d.open)}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Fan out response attempts</DialogTitle>
            <DialogContent>
              <Input
                type="number"
                min={2}
                max={50}
                value={attemptCountDraft}
                aria-label="Attempt count"
                onChange={(_e, d) => setAttemptCountDraft(d.value)}
              />
              <div className={styles.mutedFooter}>
                {attemptCountValid
                  ? `${parsedAttemptCount} response leaves`
                  : 'Choose 2 to 50 response leaves'}
              </div>
            </DialogContent>
            <DialogActions>
              <Button appearance="secondary" onClick={() => setAttemptDialogOpen(false)}>
                Cancel
              </Button>
              <Button appearance="primary" disabled={!attemptCountValid} onClick={createAttemptFan}>
                Create
              </Button>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </CardFrame>
  )
}
