// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * TreeCanvas — react-flow scaffold for a single ConversationTree.
 *
 * Pipeline: `conversationTreeToReactFlow(tree)` (pure shape) →
 * `applyStackCollapse(shape, tree, collapsedFanIds)` (filter +
 * `stackedSummary` decoration) → `useShapeMemoizedLayout(...)` (cached
 * Buchheim-Walker layout). Layout is keyed on a derived shape-key so
 * UI-state changes that don't alter shape (Pick clicks, wave-state
 * flips) re-render cards without re-running layout. Per-node action
 * callbacks (PR5c) ride through the ActionCallbacksContext.
 */

import { useCallback, useMemo, useState } from 'react'
import { ReactFlow, ReactFlowProvider } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { ActionCallbacks } from './actionRail'
import { ActionCallbacksContext } from './actionCallbacksContext'
import { applyStackCollapse } from './applyStackCollapse'
import {
  AvailableConvertersContext,
  type AvailableConvertersValue,
} from './availableConvertersContext'
import {
  conversationTreeToReactFlow,
  type TreeFlowEdge,
  type TreeFlowNode,
} from './conversationTreeToReactFlow'
import { defaultCollapsedFanIds } from './fanStack'
import { layoutTree, type LayoutNode } from './layoutTree'
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
  /**
   * Host-supplied list of converters available to UserTurn cards'
   * `⚡ Converter palette` (spec §2.2; PR5h.7). When omitted or empty,
   * the ⚡ button does not render. Host typically pre-fetches via
   * `convertersApi.listConverters` and re-passes here.
   */
  availableConverters?: AvailableConvertersValue
}

export function TreeCanvas({ tree, actionCallbacks, availableConverters }: TreeCanvasProps) {
  // Per-canvas collapse state for the Fan-Children Stack. Seeded from
  // defaultCollapsedFanIds the first time a particular tree id mounts;
  // toggling persists for the canvas's lifetime. Re-keyed on tree.id so
  // a swap to a different tree restarts with that tree's default set
  // (not carried over from the prior tree).
  const [collapsedFanIds, setCollapsedFanIds] = useState<Set<ConversationTreeNodeId>>(
    () => defaultCollapsedFanIds(tree),
  )
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

  // Shape pass: pure 1:1 mapping. Recomputes on every tree ref change
  // (state flips create new tree refs), but the work is cheap O(n) and
  // we need fresh `data.node` references for cards to re-render.
  const shape = useMemo(() => conversationTreeToReactFlow(tree), [tree])

  // Decoration pass: filter collapsed-fan descendants + attach
  // `stackedSummary` (computed from current tree state).
  const decorated = useMemo(
    () => applyStackCollapse(shape, tree, collapsedFanIds),
    [shape, tree, collapsedFanIds],
  )

  // Layout: memoized on a shape-key (node ids + edge ids) so state-only
  // changes (Pick clicks, wave-state flips) don't force a re-layout.
  // The reviewer's bundle B+D: split adapter from collapse, key layout
  // on shape rather than reference, so PR6 wave-state churn doesn't
  // re-layout per leaf.
  const positions = useShapeMemoizedLayout(decorated.nodes, decorated.edges)

  // Apply positions onto each node. New node refs let react-flow's
  // reconciler detect changes; positions are the cached map identity,
  // so node `position` objects are stable when layout didn't re-run.
  const nodes = useMemo(
    () =>
      decorated.nodes.map((n) => {
        const p = positions.get(n.id)
        return p === undefined ? n : { ...n, position: { x: p.x, y: p.y } }
      }),
    [decorated.nodes, positions],
  )

  return (
    <div
      data-testid="tree-canvas"
      data-tree-id={decorated.treeId}
      style={{ width: '100%', height: '100%' }}
    >
      <ActionCallbacksContext.Provider value={actionCallbacks ?? null}>
        <AvailableConvertersContext.Provider value={availableConverters ?? null}>
          <StackCollapseContext.Provider value={stackContextValue}>
            <ReactFlowProvider>
              <ReactFlow
                nodes={nodes}
                edges={decorated.edges}
                nodeTypes={treeNodeTypes}
                edgeTypes={treeEdgeTypes}
                fitView
              />
            </ReactFlowProvider>
          </StackCollapseContext.Provider>
        </AvailableConvertersContext.Provider>
      </ActionCallbacksContext.Provider>
    </div>
  )
}

// Layout cache keyed on a derived shape-key string. Returns the same
// `positions` Map reference across renders where the shape (node ids +
// edge ids) is unchanged — even when the input arrays are new refs.
// useMemo keyed on the shape-key is enough: React's cache may be
// discarded under memory pressure (rare in practice), so a 60-leaf
// wave whose layout cache is dropped just re-runs d3-hierarchy once
// per render until the next stable frame. Layout is sub-ms; the perf
// floor is acceptable.
function useShapeMemoizedLayout(
  nodes: ReadonlyArray<TreeFlowNode>,
  edges: ReadonlyArray<TreeFlowEdge>,
): Map<string, LayoutNode> {
  const shapeKey = useMemo(
    () =>
      `${nodes.length}:${nodes.map((n) => n.id).join(',')}|${edges.length}:${edges.map((e) => e.id).join(',')}`,
    [nodes, edges],
  )
  return useMemo(
    () => layoutTree(nodes, edges),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [shapeKey],
  )
}
