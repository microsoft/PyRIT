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

// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * TreeCanvas — react-flow scaffold for a single ConversationTree.
 *
 * Wraps `<ReactFlow />` with the adapter's output. Per-node components
 * register in PR5b's `nodeTypes` prop; layout (PR5g) wraps this with a
 * d3-hierarchy positioning pass. Per-node action callbacks (PR5c) ride
 * through the ActionCallbacksContext so cards opt in to rail render
 * without the adapter needing to know about them.
 */

import { useMemo } from 'react'
import { ReactFlow, ReactFlowProvider } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { ActionCallbacks } from './actionRail'
import { ActionCallbacksContext } from './actionCallbacksContext'
import { conversationTreeToReactFlow } from './conversationTreeToReactFlow'
import { treeEdgeTypes } from './treeEdgeTypes'
import { treeNodeTypes } from './treeNodeTypes'
import type { ConversationTree } from '../../runner/treeTypes'

export interface TreeCanvasProps {
  tree: ConversationTree
  /**
   * Per-node action callbacks. Optional — when omitted, cards do not
   * render the action rail at all (preserves the PR5a/PR5b "display
   * only" use case). When supplied, each undefined callback hides the
   * corresponding button per the per-callback opt-in rules in ActionRail.
   */
  actionCallbacks?: ActionCallbacks
}

export function TreeCanvas({ tree, actionCallbacks }: TreeCanvasProps) {
  // Re-adapt on every tree-prop change. React-flow's reconciler keys on
  // node id; the adapter guarantees stable ids, so a re-render adds /
  // removes elements without unmounting unchanged nodes. The adapter
  // does NOT depend on actionCallbacks (those ride through context),
  // so callback-prop changes don't force re-adaption.
  const { treeId, nodes, edges } = useMemo(() => conversationTreeToReactFlow(tree), [tree])

  return (
    <div
      data-testid="tree-canvas"
      data-tree-id={treeId}
      style={{ width: '100%', height: '100%' }}
    >
      <ActionCallbacksContext.Provider value={actionCallbacks ?? null}>
        <ReactFlowProvider>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={treeNodeTypes}
            edgeTypes={treeEdgeTypes}
            fitView
          />
        </ReactFlowProvider>
      </ActionCallbacksContext.Provider>
    </div>
  )
}
