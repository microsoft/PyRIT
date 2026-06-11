// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `layoutTree` — the Buchheim-Walker layout pass that converts
 * the adapter's flat nodes+edges into a Map of node-id → {x, y} screen
 * coordinates.
 *
 * V1.0 ships plain `d3-hierarchy.tree()` (Buchheim-Walker over the
 * whole tree). Main-path pinning (V1.1) is a separate layer; the V1.0
 * layout owns position only — dims are placeholder via the adapter
 * (PR5d/e), and PR5g doesn't touch them.
 *
 * Pinned contracts:
 *   - Every visible node receives a coordinate
 *   - Root sits at top (y = 0 or the configured top); children below
 *   - Single chain (root → child → grandchild) renders as a vertical
 *     line (every node shares the same x)
 *   - Siblings under one parent have distinct x; symmetric placement
 *     (left of parent / right of parent)
 *   - Nested subtrees do not collide horizontally (Buchheim-Walker
 *     contour interleaving)
 *   - Empty/single-node trees yield sensible results
 *   - Layout is deterministic (identical input → identical output)
 *   - The hidden-fan-children case (PR5e adapter pre-filters them) is
 *     handled correctly — layout sees only the visible subset
 */

import { layoutTree, type LayoutNode } from './layoutTree'
import { applyStackCollapse } from './applyStackCollapse'
import { conversationTreeToReactFlow } from './conversationTreeToReactFlow'
import {
  mkFan,
  mkRoot,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
} from '../../runner/testHelpers'

// Helper: turn the layout result into a quick `id → {x, y}` lookup.
function asPositions(layout: ReadonlyMap<string, LayoutNode>): Map<string, { x: number; y: number }> {
  const out = new Map<string, { x: number; y: number }>()
  for (const [id, n] of layout) out.set(id, { x: n.x, y: n.y })
  return out
}

// ============================================================================
// 1. Every visible node gets a coordinate
// ============================================================================

describe('layoutTree — coverage', () => {
  it('returns a position for every node the adapter emits', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const layout = layoutTree(nodes, edges)
    expect(layout.size).toBe(nodes.length)
    for (const n of nodes) {
      expect(layout.has(n.id)).toBe(true)
    }
  })

  it('positions only the visible subset when collapse has filtered fan children', () => {
    // Collapsed fan: applyStackCollapse drops s_a, s_b, s_c. Layout
    // should NOT try to position them (they're not in the input).
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    const shape = conversationTreeToReactFlow(tree)
    const { nodes, edges } = applyStackCollapse(shape, tree, new Set([nodeId('f')]))
    const layout = layoutTree(nodes, edges)
    expect(layout.size).toBe(3) // r, u, f only
    expect(layout.has('s_a')).toBe(false)
    expect(layout.has('s_b')).toBe(false)
    expect(layout.has('s_c')).toBe(false)
  })

  it('handles a single-node tree (root only)', () => {
    const tree = mkTree('r', [mkRoot('r')])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const layout = layoutTree(nodes, edges)
    expect(layout.size).toBe(1)
    expect(layout.has('r')).toBe(true)
  })

  it('returns an empty map for zero-node input (defensive)', () => {
    const layout = layoutTree([], [])
    expect(layout.size).toBe(0)
  })

  it('defensively returns origin positions when every node has a parent (cycle / malformed input)', () => {
    // Construct a malformed view where every node has an edge to it
    // (no root). Synthesize nodes + edges directly rather than via the
    // adapter (the adapter never produces this shape). Layout should
    // place every node at origin and not throw.
    const synthesizedNodes = [
      { id: 'a', type: 'send', position: { x: 0, y: 0 }, data: {} as never },
      { id: 'b', type: 'send', position: { x: 0, y: 0 }, data: {} as never },
    ] as unknown as Parameters<typeof layoutTree>[0]
    const synthesizedEdges = [
      // a → b AND b → a — every node has a parent in the set; no roots.
      {
        id: 'e1',
        source: 'a',
        target: 'b',
        type: 'insert',
        data: { slotIndex: 0, parentKind: 'send' },
      },
      {
        id: 'e2',
        source: 'b',
        target: 'a',
        type: 'insert',
        data: { slotIndex: 0, parentKind: 'send' },
      },
    ] as unknown as Parameters<typeof layoutTree>[1]
    const layout = layoutTree(synthesizedNodes, synthesizedEdges)
    expect(layout.size).toBe(2)
    expect(layout.get('a')).toEqual({ x: 0, y: 0 })
    expect(layout.get('b')).toEqual({ x: 0, y: 0 })
  })
})

// ============================================================================
// 2. Root at top; children below
// ============================================================================

describe('layoutTree — top-down orientation', () => {
  it('places the root at the smallest y; descendants at larger y', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const pos = asPositions(layoutTree(nodes, edges))
    const yr = pos.get('r')!.y
    const yu = pos.get('u')!.y
    const ys = pos.get('s')!.y
    expect(yu).toBeGreaterThan(yr)
    expect(ys).toBeGreaterThan(yu)
  })

  it('siblings share the same y (same generation, same row)', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const pos = asPositions(layoutTree(nodes, edges))
    const ya = pos.get('s_a')!.y
    const yb = pos.get('s_b')!.y
    const yc = pos.get('s_c')!.y
    expect(ya).toBe(yb)
    expect(yb).toBe(yc)
  })
})

// ============================================================================
// 3. Single chain: vertical line
// ============================================================================

describe('layoutTree — linear chain', () => {
  it('renders a chain of nodes with the same x (vertical line)', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r'),
      mkSend('s1', 'u1'),
      mkUserTurn('u2', 's1'),
      mkSend('s2', 'u2'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const pos = asPositions(layoutTree(nodes, edges))
    const xs = [pos.get('r')!.x, pos.get('u1')!.x, pos.get('s1')!.x, pos.get('u2')!.x, pos.get('s2')!.x]
    for (const x of xs) {
      expect(x).toBeCloseTo(xs[0], 5)
    }
  })
})

// ============================================================================
// 4. Siblings: distinct x, symmetric placement
// ============================================================================

describe('layoutTree — sibling placement', () => {
  it('siblings under one parent have distinct x', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const pos = asPositions(layoutTree(nodes, edges))
    const xa = pos.get('s_a')!.x
    const xb = pos.get('s_b')!.x
    const xc = pos.get('s_c')!.x
    expect(xa).not.toBe(xb)
    expect(xb).not.toBe(xc)
    expect(xa).not.toBe(xc)
  })

  it('odd-numbered siblings: middle child is centered over parent', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const pos = asPositions(layoutTree(nodes, edges))
    // s_b is the middle child; should share x with its parent (f) within tolerance.
    expect(pos.get('s_b')!.x).toBeCloseTo(pos.get('f')!.x, 5)
  })

  it('siblings render in left-to-right order matching their tree-iteration order', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const pos = asPositions(layoutTree(nodes, edges))
    expect(pos.get('s_a')!.x).toBeLessThan(pos.get('s_b')!.x)
    expect(pos.get('s_b')!.x).toBeLessThan(pos.get('s_c')!.x)
  })
})

// ============================================================================
// 5. Determinism
// ============================================================================

describe('layoutTree — determinism', () => {
  it('identical input → identical output across calls', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const a = asPositions(layoutTree(nodes, edges))
    const b = asPositions(layoutTree(nodes, edges))
    for (const [id, ap] of a) {
      expect(b.get(id)).toEqual(ap)
    }
  })
})

// ============================================================================
// 6. Nested fans: no horizontal collision
// ============================================================================

describe('layoutTree — nested subtree separation', () => {
  it('siblings at the same depth do not collide horizontally', () => {
    // Two siblings at depth 1 (u1, u2): their x must be distinct so they
    // don't overlap. Buchheim-Walker also pushes them apart by at least
    // `horizontalSpacing` because each is a single-node "subtree" at
    // that level.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r'),
      mkUserTurn('u2', 'r'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const pos = asPositions(layoutTree(nodes, edges))
    expect(pos.get('u1')!.x).not.toBe(pos.get('u2')!.x)
    expect(Math.abs(pos.get('u1')!.x - pos.get('u2')!.x)).toBeGreaterThan(0)
  })

  it('a wide subtree pushes its sibling subtree apart (no overlap at the wide depth)', () => {
    // Two children of r: u1 (which fans out 3-wide three levels below) and
    // u2 (a leaf). Buchheim-Walker should keep u2's x distinct from
    // every node in u1's subtree at the SAME depth as u2 (depth 1) —
    // u2 sits beside u1, not overlapping it.
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r'),
      mkFan('f', 'u1', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
      mkSend('s_c', 'f'),
      mkUserTurn('u2', 'r'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const pos = asPositions(layoutTree(nodes, edges))
    // u1 and u2 are siblings at depth 1; they MUST have distinct x.
    expect(pos.get('u1')!.x).not.toBe(pos.get('u2')!.x)
    // u2's x sits outside u1's subtree x-range (the whole point of
    // Buchheim-Walker's contour interleaving — wide subtrees push their
    // siblings apart). Check at u1's own depth: u2 is left of u1 OR
    // right of u1 (not between u1's descendants in the x-axis).
    const u1Subtree = ['u1', 'f', 's_a', 's_b', 's_c']
    const u1MinX = Math.min(...u1Subtree.map((id) => pos.get(id)!.x))
    const u1MaxX = Math.max(...u1Subtree.map((id) => pos.get(id)!.x))
    const u2x = pos.get('u2')!.x
    const u2Disjoint = u2x <= u1MinX || u2x >= u1MaxX
    expect(u2Disjoint).toBe(true)
  })
})

// ============================================================================
// 7. Configurable spacing
// ============================================================================

describe('layoutTree — spacing options', () => {
  it('verticalSpacing controls the distance between generations', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const a = asPositions(layoutTree(nodes, edges, { verticalSpacing: 100 }))
    const b = asPositions(layoutTree(nodes, edges, { verticalSpacing: 200 }))
    const dyA = a.get('u')!.y - a.get('r')!.y
    const dyB = b.get('u')!.y - b.get('r')!.y
    expect(dyB).toBeGreaterThan(dyA)
    expect(dyB / dyA).toBeCloseTo(2, 1)
  })

  it('horizontalSpacing controls the distance between siblings', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkFan('f', 'u', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f'),
      mkSend('s_b', 'f'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const a = asPositions(layoutTree(nodes, edges, { horizontalSpacing: 100 }))
    const b = asPositions(layoutTree(nodes, edges, { horizontalSpacing: 300 }))
    const dxA = Math.abs(a.get('s_a')!.x - a.get('s_b')!.x)
    const dxB = Math.abs(b.get('s_a')!.x - b.get('s_b')!.x)
    expect(dxB).toBeGreaterThan(dxA)
  })
})

// ============================================================================
// 8. TreeCanvas integration — adapter positions get overridden by layout
// ============================================================================

describe('TreeCanvas integration — layout overrides adapter placeholder positions', () => {
  // The placeholder positions emitted by the adapter (PR5a/d) are all
  // (0, 0). The layout pass MUST override them in TreeCanvas before
  // react-flow renders; otherwise every node would stack at the origin.
  // We can't observe react-flow's rendered positions in jsdom (the
  // viewport math depends on layout), but we can observe the layoutTree
  // result + assert TreeCanvas calls it on the adapter output.
  //
  // The cheap integration probe: run conversationTreeToReactFlow ourselves,
  // run layoutTree on the result, and assert at least one node moved off
  // (0, 0). That proves the layout pass produces non-trivial coords on
  // the same input TreeCanvas would feed it.
  it('layout produces non-(0,0) positions for a multi-node tree', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    const pos = asPositions(layoutTree(nodes, edges))
    // At least one node must have non-zero y (descendants are pushed down).
    const anyNonZeroY = Array.from(pos.values()).some((p) => p.y !== 0)
    expect(anyNonZeroY).toBe(true)
  })
})
