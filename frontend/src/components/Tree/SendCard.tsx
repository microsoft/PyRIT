// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import type { NodeProps } from '@xyflow/react'

import type { SendNode } from '../../runner/treeTypes'
import type { TreeFlowNode } from './conversationTreeToReactFlow'
import { CardBody, CardFrame, MetaRow } from './cardFrame'
import { useNodeCardStyles } from './nodeCards.styles'

type SendProps = NodeProps<Extract<TreeFlowNode, { type: 'send' }>>

export function SendCard({ data, selected }: SendProps) {
  const node: SendNode = data.node
  const styles = useNodeCardStyles()
  const kindLabel = node.state === 'draft' || node.state === 'edited' ? 'Send' : 'Assistant response'
  return (
    <CardFrame
      kindLabel={kindLabel}
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
      fanChildInfo={data.fanChildInfo}
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
