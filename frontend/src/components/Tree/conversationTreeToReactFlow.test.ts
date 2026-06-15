// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `conversationTreeToReactFlow` — the pure adapter that maps a
 * domain ConversationTree (runner-shape: nodes + edges + rootId) onto the
 * react-flow Node/Edge shape the canvas consumes.
 *
 * Scope:
 *   - 1:1 node mapping (one react-flow Node per ConversationTreeNode)
 *   - 1:1 edge mapping (one react-flow Edge per ConversationTreeEdge)
 *   - kind → react-flow node-type passthrough so PR5b's node-component
 *     registry can register by kind
 *   - slotIndex + parentKind carried on edge data
 *   - placeholder positions (layout overrides via d3-hierarchy)
 *
 * Out of scope:
 *   - Fan-Children Stack collapse → see `applyStackCollapse.test.ts`
 *   - node components, layout, action rails, edge chips
 */

import { conversationTreeToReactFlow } from './conversationTreeToReactFlow'
import {
  mkConverterNode,
  mkEdge,
  mkFan,
  mkImport,
  mkRoot,
  mkScore,
  mkSend,
  mkTree,
  mkUserTurn,
  nodeId,
  treeId,
} from '../../runner/testHelpers'

// ============================================================================
// 1:1 node mapping
// ============================================================================

describe('conversationTreeToReactFlow — node mapping', () => {
  it('returns one react-flow node per ConversationTreeNode', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkSend('s', 'u'),
    ])
    const { nodes } = conversationTreeToReactFlow(tree)
    expect(nodes).toHaveLength(3)
    expect(nodes.map((n) => n.id).sort()).toEqual(['r', 's', 'u'])
  })

  it("each node's `type` is the source node's `kind`", () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r'),
      mkConverterNode('c', 'u'),
      mkSend('s', 'c'),
      mkFan('f', 's'),
      mkScore('sc', 'f'),
    ])
    const { nodes } = conversationTreeToReactFlow(tree)
    const byId = new Map(nodes.map((n) => [n.id, n]))
    expect(byId.get('r')?.type).toBe('root_prompt')
    expect(byId.get('u')?.type).toBe('user_turn')
    expect(byId.get('c')?.type).toBe('converter')
    expect(byId.get('s')?.type).toBe('send')
    expect(byId.get('f')?.type).toBe('fan')
    expect(byId.get('sc')?.type).toBe('score')
  })

  it('handles ImportMessageNode as the root', () => {
    const tree = mkTree('imp', [mkImport('imp'), mkUserTurn('u', 'imp')])
    const { nodes } = conversationTreeToReactFlow(tree)
    const imp = nodes.find((n) => n.id === 'imp')
    expect(imp?.type).toBe('import_message')
  })

  it("each node's `data.node` is the source ConversationTreeNode (by identity)", () => {
    // PR5b's node components read params + state off `data.node`. The adapter
    // must not clone or restructure the node — a re-render must see the same
    // ConversationTreeNode reference to allow downstream useMemo memoization.
    const root = mkRoot('r', { text: 'hello' })
    const turn = mkUserTurn('u', 'r', { text: 'follow-up' })
    const converter = mkConverterNode('c', 'u')
    const send = mkSend('s', 'c', undefined, { state: 'edited' })
    const tree = mkTree('r', [root, turn, converter, send])
    const { nodes } = conversationTreeToReactFlow(tree)
    const byId = new Map(nodes.map((n) => [n.id, n]))
    expect(byId.get('r')?.data.node).toBe(root)
    expect(byId.get('u')?.data.node).toBe(turn)
    expect(byId.get('c')?.data.node).toBe(converter)
    expect(byId.get('s')?.data.node).toBe(send)
  })

  it("each node gets a placeholder { x: 0, y: 0 } position (PR5g layout overrides)", () => {
    // react-flow tolerates same-position nodes (they stack at origin). The
    // PR5g d3-hierarchy layout pass overrides via setNodes(layoutedNodes).
    // Until PR5g lands, the adapter's only obligation is non-undefined
    // positions so react-flow doesn't throw at mount.
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const { nodes } = conversationTreeToReactFlow(tree)
    for (const n of nodes) {
      expect(n.position).toEqual({ x: 0, y: 0 })
    }
  })
})

// ============================================================================
// 1:1 edge mapping
// ============================================================================

describe('conversationTreeToReactFlow — edge mapping', () => {
  it('returns one react-flow edge per ConversationTreeEdge', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])
    const { edges } = conversationTreeToReactFlow(tree)
    expect(edges).toHaveLength(2) // r→u, u→s
  })

  it("each edge's `source`/`target` mirror the domain edge's parentId/childId", () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])
    const { edges } = conversationTreeToReactFlow(tree)
    const byPair = new Map(edges.map((e) => [`${e.source}->${e.target}`, e]))
    expect(byPair.has('r->u')).toBe(true)
    expect(byPair.has('u->s')).toBe(true)
  })

  it("each edge's `id` matches the source ConversationTreeEdge.id (stable across renders)", () => {
    // Stable ids are load-bearing for react-flow's reconciler — edges that
    // change id between renders force a full unmount/remount, which kills
    // edge-hover state (the PR5d `+` chip's visibility).
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const { edges } = conversationTreeToReactFlow(tree)
    expect(edges[0].id).toBe(tree.edges[0].id)
  })

  it("each edge carries `data.slotIndex` (default 0 for non-fan parents)", () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])
    const { edges } = conversationTreeToReactFlow(tree)
    for (const e of edges) {
      expect(e.data?.slotIndex).toBe(0)
    }
  })

  it('Fan parent: per-child edges carry distinct slotIndex values from the domain edge', () => {
    // mkTree's auto-numbering assigns slotIndex 0..N-1 to fan children in
    // insertion order. The adapter must surface these on edge data so the
    // PR5e Fan-Children Stack predicate ("group children by slot in source-
    // domain edge order") + PR5f Pick/Unpick (writes promotedChildSlotIndex)
    // can read it directly.
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
    const { edges } = conversationTreeToReactFlow(tree)
    const fanEdges = edges.filter((e) => e.source === 'f').sort((a, b) =>
      (a.data?.slotIndex ?? 0) - (b.data?.slotIndex ?? 0),
    )
    expect(fanEdges.map((e) => e.target)).toEqual(['s_a', 's_b', 's_c'])
    expect(fanEdges.map((e) => e.data?.slotIndex)).toEqual([0, 1, 2])
  })

  it("uses 'insert' edge type (TreeCanvas maps it to the custom InsertEdge that wraps smoothstep + chip)", () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const { edges } = conversationTreeToReactFlow(tree)
    for (const e of edges) {
      expect(e.type).toBe('insert')
    }
  })

  it('explicit edges with non-default slotIndex round-trip through the adapter', () => {
    // Real-world case: a fan with explicit slot indices (e.g., after a
    // deletion that left a tombstone). mkTree with `edges` override gives us
    // that surface.
    const tree = mkTree(
      'r',
      [
        mkRoot('r'),
        mkUserTurn('u', 'r'),
        mkFan('f', 'u', { axis: 'attempt', variants: [] }),
        mkSend('s_old', 'f'),
        mkSend('s_new', 'f'),
      ],
      {
        edges: [
          mkEdge('r', 'u', 0),
          mkEdge('u', 'f', 0),
          // s_old was originally slot 0 but kept after a deletion-then-readd;
          // s_new is the freshly allocated slot 7.
          mkEdge('f', 's_old', 3),
          mkEdge('f', 's_new', 7),
        ],
      },
    )
    const { edges } = conversationTreeToReactFlow(tree)
    const fanEdges = edges
      .filter((e) => e.source === 'f')
      .sort((a, b) => (a.data?.slotIndex ?? 0) - (b.data?.slotIndex ?? 0))
    expect(fanEdges.map((e) => [e.target, e.data?.slotIndex])).toEqual([
      ['s_old', 3],
      ['s_new', 7],
    ])
  })
})

// ============================================================================
// Edge cases
// ============================================================================

describe('conversationTreeToReactFlow — edge cases', () => {
  it('root-only tree: one node, zero edges', () => {
    const tree = mkTree('r', [mkRoot('r')])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    expect(nodes).toHaveLength(1)
    expect(edges).toHaveLength(0)
  })

  it('does not mutate the input tree', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const beforeNodes = tree.nodes
    const beforeEdges = tree.edges
    conversationTreeToReactFlow(tree)
    expect(tree.nodes).toBe(beforeNodes)
    expect(tree.edges).toBe(beforeEdges)
  })

  it('handles a wide tree with multiple Fan-children paths', () => {
    // r → u → f(attempt) → [s1, s2, s3] each with their own UserTurn child
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
      mkSend('s1', 'f'),
      mkUserTurn('u1', 's1'),
      mkSend('s2', 'f'),
      mkUserTurn('u2', 's2'),
      mkSend('s3', 'f'),
      mkUserTurn('u3', 's3'),
    ])
    const { nodes, edges } = conversationTreeToReactFlow(tree)
    expect(nodes).toHaveLength(9)
    expect(edges).toHaveLength(8)
  })

  it('treeId is exposed on the result for caller convenience', () => {
    // Callers (TreeCanvas, future PR5g layout) carry the treeId alongside
    // the adapted nodes/edges to scope sink writes. Surfacing it here saves
    // every caller from re-reading tree.id and matches the runner's
    // convention.
    const tree = mkTree('r', [mkRoot('r')], { id: 't-42' })
    const result = conversationTreeToReactFlow(tree)
    expect(result.treeId).toBe(treeId('t-42'))
  })

  it("data.node is typed so PR5b's node components can narrow by kind", () => {
    // Type-level check: each node's `data.node.kind` should narrow to the
    // node type's discriminant. The adapter outputs a single union that
    // node-component dispatchers can switch over.
    const tree = mkTree('r', [mkRoot('r')])
    const { nodes } = conversationTreeToReactFlow(tree)
    const n = nodes[0]
    // Without the right type alignment, this would not compile.
    if (n.type === 'root_prompt') {
      expect(n.data.node.kind).toBe('root_prompt')
      expect(n.data.node.params.text).toBe('root prompt')
    }
    // Round-trip the id through the brand-aware nodeId helper.
    expect(n.id).toBe(nodeId('r'))
  })
})

// ============================================================================
// Adapter contract — malformed edge surfaces loudly (PR5h.8 review)
// ============================================================================
//
// PR5h reviewer Finding H: pre-PR5h.8 `toFlowEdge` silently fell back to
// `parentKind ?? 'root_prompt'` when an edge's parentId wasn't in the tree's
// node set — steering the InsertEdge menu to the wrong kind-specific options.
// The adapter's contract is `tree.edges must reference tree.nodes`; violating
// it is a malformed-tree bug worth crashing for, not silently massaging.

describe('conversationTreeToReactFlow — malformed edge contract', () => {
  it('throws when an edge references a parentId not present in tree.nodes', () => {
    const tree = mkTree(
      'r',
      [mkRoot('r'), mkUserTurn('u', 'r')],
      {
        edges: [
          mkEdge('r', 'u'),
          // Synthetic malformed edge: 'ghost' isn't a node in the tree.
          mkEdge('ghost', 'u'),
        ],
      },
    )
    expect(() => conversationTreeToReactFlow(tree)).toThrow(/ghost/)
  })
})

