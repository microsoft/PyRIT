// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * TreeCanvas — react-flow scaffold for a single ConversationTree.
 *
 * Wraps `<ReactFlow />` with the adapter's output. Per-node components
 * register in PR5b's `nodeTypes` prop; layout (PR5g) wraps this with a
 * d3-hierarchy positioning pass. Interactivity (action rail, edge `+`
 * chip) lands in PR5b-d.
 *
 * Until PR5b registers concrete node components, react-flow renders each
 * domain node with its default node card (showing the node id). This is
 * enough to verify the scaffold mounts.
 */

import { useMemo } from 'react'
import { ReactFlow, ReactFlowProvider } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { conversationTreeToReactFlow } from './conversationTreeToReactFlow'
import type { ConversationTree } from '../../runner/treeTypes'

export interface TreeCanvasProps {
  tree: ConversationTree
}

export function TreeCanvas({ tree }: TreeCanvasProps) {
  // Re-adapt on every tree-prop change. React-flow's reconciler keys on
  // node id; the adapter guarantees stable ids, so a re-render adds /
  // removes elements without unmounting unchanged nodes.
  const { treeId, nodes, edges } = useMemo(() => conversationTreeToReactFlow(tree), [tree])

  return (
    <div
      data-testid="tree-canvas"
      data-tree-id={treeId}
      style={{ width: '100%', height: '100%' }}
    >
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          // PR5b registers per-kind node components here. Default node
          // type renders the id; sufficient for the scaffold.
          // nodeTypes={...}
          // PR5d adds the edge `+` chip via edgeTypes; default smoothstep
          // is fine for the scaffold.
          // edgeTypes={...}
          fitView
        />
      </ReactFlowProvider>
    </div>
  )
}
