// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import type { NodeProps } from '@xyflow/react'

import type { ImportMessageNode } from '../../runner/treeTypes'
import type { TreeFlowNode } from './conversationTreeToReactFlow'
import { CardFrame, MetaRow } from './cardFrame'

type ImportMessageProps = NodeProps<Extract<TreeFlowNode, { type: 'import_message' }>>

export function ImportMessageCard({ data, selected }: ImportMessageProps) {
  const node: ImportMessageNode = data.node
  return (
    <CardFrame
      kindLabel="Imported message"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
      fanChildInfo={data.fanChildInfo}
      showTargetHandle={false}
    >
      <MetaRow label="source" value={node.params.sourceConversationId} />
      <MetaRow label="cutoff" value={String(node.params.cutoffIndex)} />
    </CardFrame>
  )
}
