// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Buchheim-Walker tree layout via `d3-hierarchy`.
 *
 * V1.0: plain `d3-hierarchy.tree()` over the adapter's nodes + edges.
 * Returns a Map of node-id → screen coordinates. The layout pass owns
 * positions only — node dimensions are placeholder via the adapter
 * (PR5d), and PR5g leaves them alone.
 *
 * The adapter pre-filters Fan-Children Stack descendants (PR5e), so
 * layout only sees visible nodes. d3-hierarchy's `stratify()` requires
 * a single root and no orphans; our filtered output meets both because
 * collapse drops subtrees beneath the fan node, not the fan itself.
 *
 * Main-path pinning + adaptive collapse are V1.1 layers; this module
 * stays scoped to "convert adapter output into coordinates."
 */

import { stratify, tree, type HierarchyPointNode } from 'd3-hierarchy'

import type {
  TreeFlowEdge,
  TreeFlowNode,
} from './conversationTreeToReactFlow'

export interface LayoutNode {
  x: number
  y: number
}

export interface LayoutOptions {
  /** Default 220 — wider than the card's min-width (220) to keep cards from touching. */
  horizontalSpacing?: number
  /** Default 140 — generous enough for the card height (~80) plus action rail + meta rows. */
  verticalSpacing?: number
}

const DEFAULT_HORIZONTAL_SPACING = 220
const DEFAULT_VERTICAL_SPACING = 140

/**
 * Compute Buchheim-Walker tree-layout coordinates for the adapter's
 * node + edge output. Returns an empty Map for zero-node input; uses
 * (0, 0) for a single-node tree (no descendants to push off-origin).
 *
 * The `edges` parameter is the source of the parent→child relation
 * (rather than each node's `parentId`, which lives on the domain node
 * but not the react-flow node). This lets layout consume the SAME
 * filtered view react-flow gets — if the adapter dropped an edge,
 * layout treats the child as a root.
 */
export function layoutTree(
  nodes: ReadonlyArray<TreeFlowNode>,
  edges: ReadonlyArray<TreeFlowEdge>,
  options: LayoutOptions = {},
): Map<string, LayoutNode> {
  const result = new Map<string, LayoutNode>()
  if (nodes.length === 0) return result

  const horizontal = options.horizontalSpacing ?? DEFAULT_HORIZONTAL_SPACING
  const vertical = options.verticalSpacing ?? DEFAULT_VERTICAL_SPACING

  // Build parent lookup. A node whose parent id is absent from the
  // filtered node set (e.g., a node whose parent was hidden by stack
  // collapse) is treated as a root — d3-hierarchy refuses orphans
  // pointing at non-existent ids. In practice this only happens if a
  // caller passes a malformed view; the adapter never produces it.
  const visibleIds = new Set(nodes.map((n) => n.id))
  const parentByChildId = new Map<string, string>()
  for (const edge of edges) {
    if (!visibleIds.has(edge.source) || !visibleIds.has(edge.target)) continue
    parentByChildId.set(edge.target, edge.source)
  }

  // d3-hierarchy's stratify requires exactly one root. If our filtered
  // input has multiple roots (e.g., a disconnected forest), we layout
  // each root's subtree independently and translate them horizontally
  // so they don't overlap. V1.0 trees always have one root, so the
  // multi-root branch is a defensive fall-through.
  const rootIds = nodes.map((n) => n.id).filter((id) => !parentByChildId.has(id))
  if (rootIds.length === 0) {
    // No roots — defensive (every node has a parent inside the set, which
    // implies a cycle; d3-hierarchy would throw anyway). Place every
    // node at origin so the canvas doesn't crash.
    for (const n of nodes) result.set(n.id, { x: 0, y: 0 })
    return result
  }

  // V1.0 trees always have one root (per the domain contract). A
  // multi-root forest would indicate a malformed adapter view; lay
  // out each subtree at the origin and let visual overlap surface
  // the bug. The defensive single-fallback path here is shorter than
  // a from-scratch "translate each subtree" layout would be, and
  // preserves a chance to detect the malformation rather than silently
  // hiding it via clever shifts.
  for (const rootId of rootIds) {
    layoutOneRoot(nodes, parentByChildId, rootId, horizontal, vertical, 0, result)
  }
  return result
}

// ============================================================================
// Private helpers
// ============================================================================

function layoutOneRoot(
  nodes: ReadonlyArray<TreeFlowNode>,
  parentByChildId: ReadonlyMap<string, string>,
  rootId: string,
  horizontal: number,
  vertical: number,
  baseY: number,
  out: Map<string, LayoutNode>,
): void {
  // d3-hierarchy.stratify wants per-record (id, parentId?) shape. Build
  // a stratifier that reads the parent map (NOT the domain node's
  // parentId — the filtered view's edge set is the source of truth).
  const stratifier = stratify<{ id: string }>()
    .id((n) => n.id)
    .parentId((n) => parentByChildId.get(n.id))
  // Filter to this root's subtree only when called from the multi-root
  // path; in the common single-root case `nodes` already IS the whole
  // tree and the filter is a no-op.
  const subtreeIds = collectSubtree(rootId, parentByChildId, new Set(nodes.map((n) => n.id)))
  const subtreeRecords = nodes.filter((n) => subtreeIds.has(n.id)).map((n) => ({ id: n.id as string }))
  if (subtreeRecords.length === 0) return

  const hierarchy = stratifier(subtreeRecords)
  // nodeSize sets a fixed [width, height] block per node — d3-hierarchy
  // packs siblings horizontally with `horizontal` separation, parents
  // and children with `vertical` separation. Operator-friendly defaults
  // sized to match the card's min-width (220) + the action-rail body.
  const layout = tree<{ id: string }>().nodeSize([horizontal, vertical])
  const positioned = layout(hierarchy)

  positioned.each((pn: HierarchyPointNode<{ id: string }>) => {
    out.set(pn.data.id, { x: pn.x, y: pn.y + baseY })
  })
}

function collectSubtree(
  rootId: string,
  parentByChildId: ReadonlyMap<string, string>,
  visibleIds: ReadonlySet<string>,
): Set<string> {
  // Invert parentByChildId once: childrenOf.
  const childrenOf = new Map<string, string[]>()
  for (const [child, parent] of parentByChildId) {
    const arr = childrenOf.get(parent)
    if (arr === undefined) childrenOf.set(parent, [child])
    else arr.push(child)
  }
  const out = new Set<string>([rootId])
  const queue: string[] = [rootId]
  while (queue.length > 0) {
    const id = queue.shift() as string
    const children = childrenOf.get(id) ?? []
    for (const c of children) {
      if (!visibleIds.has(c)) continue
      if (out.has(c)) continue
      out.add(c)
      queue.push(c)
    }
  }
  return out
}
