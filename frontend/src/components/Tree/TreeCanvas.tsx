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
 * without the adapter needing to know about them. PR5e adds the Fan-
 * Children Stack collapse state (per-canvas, seeded from
 * `defaultCollapsedFanIds`) provided via StackCollapseContext.
 */

import { useCallback, useMemo, useState } from 'react'
import { ReactFlow, ReactFlowProvider } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { ActionCallbacks } from './actionRail'
import { ActionCallbacksContext } from './actionCallbacksContext'
import { conversationTreeToReactFlow } from './conversationTreeToReactFlow'
import { defaultCollapsedFanIds } from './fanStack'
import {
  StackCollapseContext,
  type StackCollapseValue,
} from './stackCollapseContext'
import { treeEdgeTypes } from './treeEdgeTypes'
import { treeNodeTypes } from './treeNodeTypes'
import type {
  ConversationTree,
  ConversationTreeId,
  ConversationTreeNodeId,
} from '../../runner/treeTypes'

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
  // PR5e: per-canvas collapse state for the Fan-Children Stack. Seeded
  // from defaultCollapsedFanIds the first time a particular tree id
  // mounts; toggling persists for the canvas's lifetime. Re-keyed on
  // tree.id so a swap to a different tree restarts with that tree's
  // default-collapsed set (not carried over from the prior tree).
  const [collapsedFanIds, setCollapsedFanIds] = useState<Set<ConversationTreeNodeId>>(
    () => defaultCollapsedFanIds(tree),
  )
  // When the operator swaps to a different tree, reseed the collapse set.
  // The previous canvas's collapse decisions don't apply (different node
  // ids). We watch tree.id rather than the tree reference because the
  // runner mutates trees in place during waves.
  const [lastTreeId, setLastTreeId] = useState<ConversationTreeId>(tree.id)
  if (lastTreeId !== tree.id) {
    setLastTreeId(tree.id)
    setCollapsedFanIds(defaultCollapsedFanIds(tree))
  }

  const toggleStack = useCallback((fanNodeId: ConversationTreeNodeId) => {
    setCollapsedFanIds((prev) => {
      const next = new Set(prev)
      if (next.has(fanNodeId)) next.delete(fanNodeId)
      else next.add(fanNodeId)
      return next
    })
  }, [])

  const stackContextValue = useMemo<StackCollapseValue>(
    () => ({ collapsedFanIds, toggleStack }),
    [collapsedFanIds, toggleStack],
  )

  // Re-adapt when tree changes OR when the collapse set changes (a
  // toggle hides/shows nodes). React-flow's reconciler keys on node id
  // and the adapter guarantees stable ids.
  const { treeId, nodes, edges } = useMemo(
    () => conversationTreeToReactFlow(tree, { collapsedFanIds }),
    [tree, collapsedFanIds],
  )

  return (
    <div
      data-testid="tree-canvas"
      data-tree-id={treeId}
      style={{ width: '100%', height: '100%' }}
    >
      <ActionCallbacksContext.Provider value={actionCallbacks ?? null}>
        <StackCollapseContext.Provider value={stackContextValue}>
          <ReactFlowProvider>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={treeNodeTypes}
              edgeTypes={treeEdgeTypes}
              fitView
            />
          </ReactFlowProvider>
        </StackCollapseContext.Provider>
      </ActionCallbacksContext.Provider>
    </div>
  )
}
