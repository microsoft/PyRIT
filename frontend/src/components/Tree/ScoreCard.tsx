// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import type { NodeProps } from '@xyflow/react'

import type { ScoreNode } from '../../runner/treeTypes'
import type { TreeFlowNode } from './conversationTreeToReactFlow'
import { CardFrame, MetaRow } from './cardFrame'
import { useNodeCardStyles } from './nodeCards.styles'

type ScoreProps = NodeProps<Extract<TreeFlowNode, { type: 'score' }>>

export function ScoreCard({ data, selected }: ScoreProps) {
  const node: ScoreNode = data.node
  const styles = useNodeCardStyles()
  return (
    <CardFrame
      kindLabel="Score"
      state={node.state}
      nodeId={node.id}
      selected={selected}
      branchLabel="Branch from here"
      fanChildInfo={data.fanChildInfo}
    >
      <MetaRow label="scorer" value={node.params.scorerType} />
      <div className={styles.mutedFooter}>Read-only display</div>
    </CardFrame>
  )
}
