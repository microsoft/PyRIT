// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `applyStackCollapse` — the render-time policy pass that
 * filters Fan-Children Stack descendants from a `conversationTreeToReactFlow`
 * result and attaches `stackedSummary` to each collapsed fan's data.
 *
 * Split out of the adapter in PR5h.1 so the adapter stays a pure
 * shape→shape mapping. The reviewer's B/D bundle: adapter changes only
 * on shape; layout memoizes on the adapter output; collapse + summary
 * are decoration. The adapter does not see UI state and is not affected
 * by Pick or wave-state flips.
 */

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

// ============================================================================
// Empty / identity behaviour
// ============================================================================

describe('applyStackCollapse — identity when no fans are collapsed', () => {
  it('returns the input shape (same nodes + edges) when collapsedFanIds is empty', () => {
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
    const shape = conversationTreeToReactFlow(tree)
    const decorated = applyStackCollapse(shape, tree, new Set())
    expect(decorated.nodes.map((n) => n.id).sort()).toEqual(
      shape.nodes.map((n) => n.id).sort(),
    )
    expect(decorated.edges.length).toBe(shape.edges.length)
  })

  it('does not attach `stackedSummary` to any fan when collapsedFanIds is empty', () => {
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
    const shape = conversationTreeToReactFlow(tree)
    const decorated = applyStackCollapse(shape, tree, new Set())
    const fan = decorated.nodes.find((n) => n.id === nodeId('f'))!
    if (fan.type === 'fan') {
      expect(fan.data.stackedSummary).toBeUndefined()
    }
  })

  it('preserves treeId from the shape input', () => {
    const tree = mkTree('r', [mkRoot('r')], { id: 't-42' })
    const shape = conversationTreeToReactFlow(tree)
    const decorated = applyStackCollapse(shape, tree, new Set())
    expect(decorated.treeId).toBe(shape.treeId)
  })
})

// ============================================================================
// Collapse: filter descendants + attach summary
// ============================================================================

describe('applyStackCollapse — single fan collapsed', () => {
  it("filters the fan's descendant subtree (fan itself stays visible)", () => {
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
    const decorated = applyStackCollapse(shape, tree, new Set([nodeId('f')]))
    expect(decorated.nodes.map((n) => n.id).sort()).toEqual(['f', 'r', 'u'])
  })

  it('drops edges whose source or target is hidden', () => {
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
    const shape = conversationTreeToReactFlow(tree)
    const decorated = applyStackCollapse(shape, tree, new Set([nodeId('f')]))
    const pairs = decorated.edges.map((e) => `${e.source}->${e.target}`).sort()
    expect(pairs).toEqual(['r->u', 'u->f'])
  })

  it('recursively hides nested descendants under the collapsed fan', () => {
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
      mkUserTurn('u_a', 's_a'),
      mkSend('s_a2', 'u_a'),
      mkSend('s_b', 'f'),
      mkUserTurn('u_b', 's_b'),
      mkSend('s_b2', 'u_b'),
    ])
    const shape = conversationTreeToReactFlow(tree)
    const decorated = applyStackCollapse(shape, tree, new Set([nodeId('f')]))
    expect(decorated.nodes.map((n) => n.id).sort()).toEqual(['f', 'r', 'u'])
  })

  it("attaches `stackedSummary` to the collapsed fan with byState aggregation", () => {
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
      mkSend('s_a', 'f', undefined, { state: 'clean' }),
      mkSend('s_b', 'f', undefined, { state: 'failed' }),
    ])
    const shape = conversationTreeToReactFlow(tree)
    const decorated = applyStackCollapse(shape, tree, new Set([nodeId('f')]))
    const fan = decorated.nodes.find((n) => n.id === nodeId('f'))!
    if (fan.type === 'fan') {
      expect(fan.data.stackedSummary).toBeDefined()
      expect(fan.data.stackedSummary?.total).toBe(2)
      expect(fan.data.stackedSummary?.childKind).toBe('send')
      expect(fan.data.stackedSummary?.byState.clean).toBe(1)
      expect(fan.data.stackedSummary?.byState.failed).toBe(1)
    }
  })
})

// ============================================================================
// Multiple fans collapsed
// ============================================================================

describe('applyStackCollapse — multiple fans collapsed', () => {
  it('hides each collapsed fan’s subtree independently', () => {
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u1', 'r'),
      mkFan('f1', 'u1', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_a', 'f1'),
      mkSend('s_b', 'f1'),
      mkUserTurn('u2', 'r'),
      mkFan('f2', 'u2', {
        axis: 'attempt',
        variants: [
          { axis: 'attempt', payload: {} },
          { axis: 'attempt', payload: {} },
        ],
      }),
      mkSend('s_c', 'f2'),
      mkSend('s_d', 'f2'),
    ])
    const shape = conversationTreeToReactFlow(tree)
    const decorated = applyStackCollapse(
      shape,
      tree,
      new Set([nodeId('f1'), nodeId('f2')]),
    )
    expect(decorated.nodes.map((n) => n.id).sort()).toEqual(['f1', 'f2', 'r', 'u1', 'u2'])
  })
})

// ============================================================================
// Purity
// ============================================================================

describe('applyStackCollapse — purity', () => {
  it('does not mutate the input shape (shape.nodes/edges arrays + their entries)', () => {
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
    const shape = conversationTreeToReactFlow(tree)
    const beforeNodes = shape.nodes
    const beforeEdges = shape.edges
    const beforeFanData = shape.nodes.find((n) => n.id === nodeId('f'))?.data
    applyStackCollapse(shape, tree, new Set([nodeId('f')]))
    expect(shape.nodes).toBe(beforeNodes)
    expect(shape.edges).toBe(beforeEdges)
    // The shape's fan-data object must not have stackedSummary written
    // onto it — apply must clone the fan node before attaching summary.
    const afterFanData = shape.nodes.find((n) => n.id === nodeId('f'))?.data
    expect(afterFanData).toBe(beforeFanData)
    if (afterFanData && 'stackedSummary' in afterFanData) {
      expect(afterFanData.stackedSummary).toBeUndefined()
    }
  })

  it('does not mutate the input tree', () => {
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
    const beforeNodes = tree.nodes
    const beforeEdges = tree.edges
    const shape = conversationTreeToReactFlow(tree)
    applyStackCollapse(shape, tree, new Set([nodeId('f')]))
    expect(tree.nodes).toBe(beforeNodes)
    expect(tree.edges).toBe(beforeEdges)
  })
})
