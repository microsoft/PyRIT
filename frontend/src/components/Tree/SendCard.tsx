// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import type { NodeProps } from '@xyflow/react'
import { Button, Tooltip } from '@fluentui/react-components'
import { AddRegular, BranchForkRegular, FlashRegular } from '@fluentui/react-icons'

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
  const kindLabel = node.state === 'draft' || node.state === 'edited' ? 'Send' : 'Assistant response'
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
              onClick={() => callbacks.onCreateFanFromNode?.(node.id, 'attempt')}
            />
          </Tooltip>
          <Tooltip content="Fan out converters" relationship="description">
            <Button
              size="small"
              appearance="subtle"
              icon={<FlashRegular />}
              aria-label="Fan out converters"
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
      {node.params.responsePreview !== undefined && node.params.responsePreview.length > 0 && (
        <CardBody text={node.params.responsePreview} />
      )}
      {node.params.targetRegistryName !== undefined && (
        <MetaRow label="target" value={node.params.targetRegistryName} />
      )}
      {node.state === 'failed' && node.lastError !== null && (
        <div className={styles.errorPanel}>{node.lastError.message}</div>
      )}
    </CardFrame>
  )
}
